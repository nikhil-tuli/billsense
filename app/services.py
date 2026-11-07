"""Scan (extract) and judge one bill at a time. Writes only when APP_MODE=deployed."""
import threading
from contextlib import contextmanager

from app import db
from app.clients import CallResult, GeminiExtractor, MistralJudge
from app.compare import compare
from app.config import ReadOnlyError, get_settings
from app.fields import KEYS

_clients = {}
_inflight = set()
_inflight_lock = threading.Lock()
_slots = {"scan": threading.Semaphore(1), "judge": threading.Semaphore(1)}   # one provider call at a time


class Busy(RuntimeError):
    pass


def set_clients(extractor=None, judge=None):
    """Replace the extractor/judge (used by tests)."""
    _clients["extractor"], _clients["judge"] = extractor, judge


def extractor():
    if _clients.get("extractor") is None:
        _clients["extractor"] = GeminiExtractor(get_settings())
    return _clients["extractor"]


def judge():
    if _clients.get("judge") is None:
        _clients["judge"] = MistralJudge(get_settings())
    return _clients["judge"]


def _require_writes():
    if not get_settings().writes_enabled:
        raise ReadOnlyError("Read-only mode: scanning and judging only work in the deployed app.")


@contextmanager
def _exclusive(kind, bill_id):
    """Refuse (instead of queueing) a second concurrent scan/judge, so repeat clicks can't pile up threads."""
    key = (kind, bill_id)
    with _inflight_lock:
        if key in _inflight:
            raise Busy(f"{bill_id} is already being processed.")
        if not _slots[kind].acquire(blocking=False):
            raise Busy("Another bill is being processed. Try again in a few seconds.")
        _inflight.add(key)
    try:
        yield
    finally:
        with _inflight_lock:
            _inflight.discard(key)
        _slots[kind].release()


def _safe(call, *args) -> CallResult:
    try:
        return call(*args)
    except Exception as e:                                   # noqa: BLE001 - always log the attempt
        return CallResult("error", error=f"{type(e).__name__}: {e}"[:500])


def scan_bill(bill_id: str) -> dict:
    """Extract one bill from its stored image; overwrites any earlier extraction."""
    _require_writes()
    s = get_settings()
    try:
        with _exclusive("scan", bill_id):
            used = db.runs_last_24h("extractor")
            if used >= s.gemini_daily_cap:
                return {"ok": False, "message": f"Daily extraction cap reached ({used} calls in 24h, cap {s.gemini_daily_cap})."}
            img = db.get_image(bill_id)
            truth = db.get_truth(bill_id)
            if not img or truth is None:
                return {"ok": False, "message": f"{bill_id}: image or ground truth missing"}
            res = _safe(extractor().run, bytes(img["image"]), img["mime"])
            db.log_run(bill_id, "extractor", s.gemini_model, res)
            if res.status != "ok":
                return {"ok": False, "message": f"Extraction failed ({res.status}): {res.error or ''}".strip()}
            fields = {k: res.data.get(k) for k in KEYS}
            db.save_extraction(bill_id, fields, s.gemini_model, compare(truth, fields))
            return {"ok": True, "message": f"Scanned {bill_id} in {res.latency_ms / 1000:.1f}s"}
    except Busy as e:
        return {"ok": False, "message": str(e)}


def judge_bill(bill_id: str) -> dict:
    """Ask the judge model to verify the stored extraction against the bill image."""
    _require_writes()
    s = get_settings()
    try:
        with _exclusive("judge", bill_id):
            row = db.get_extraction(bill_id)
            if not row:
                return {"ok": False, "message": "Scan this bill first."}
            img = db.get_image(bill_id)
            shown = row["raw_json"] or db.extraction_fields(row)       # exactly what was scored
            res = _safe(judge().run, bytes(img["image"]), shown, img["mime"])
            db.log_run(bill_id, "judge", s.mistral_model, res)
            if res.status != "ok":
                return {"ok": False, "message": f"Judge failed ({res.status}): {res.error or ''}".strip()}
            db.save_judge(bill_id, res.data["verdicts"])
            return {"ok": True, "message": f"Judged {bill_id} in {res.latency_ms / 1000:.1f}s"}
    except Busy as e:
        return {"ok": False, "message": str(e)}
