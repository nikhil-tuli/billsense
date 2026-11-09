import pytest

from app import db, seed, services
from app.config import get_settings
from tests.conftest import FakeExtractor, FakeJudge


def test_seed_loaded_once(fresh_db):
    assert len(db.properties()) == 20 and len(db.bills()) == 20
    assert len({p["owner"] for p in db.properties()}) == 12
    from collections import Counter
    assert Counter(b["noise_level"] for b in db.bills()) == {"clean": 7, "medium": 7, "noisy": 6}
    assert db.one("SELECT count(*) AS n FROM bill_images")["n"] == 20
    assert db.one("SELECT count(*) AS n FROM ground_truth")["n"] == 20
    assert seed.seed_if_empty() is False                        # idempotent


def test_one_bill_per_property_and_unique_account(fresh_db):
    assert db.one("SELECT count(DISTINCT account_id) AS n FROM bills")["n"] == 20
    with pytest.raises(Exception):
        db.q("INSERT INTO bills (bill_id, account_id, bill_period_start, bill_period_end) "
             "SELECT 'dup', account_id, bill_period_start, bill_period_end FROM bills LIMIT 1")


def test_scan_perfect_extraction_then_judge(fresh_db):
    ex, jd = FakeExtractor(), FakeJudge()
    ex.bill_id = jd.bill_id = "bill_001"
    services.set_clients(ex, jd)
    r = services.scan_bill("bill_001")
    assert r["ok"] and db.get_bill("bill_001")["scan_status"] == "scanned"
    ev = db.eval_for("bill_001")
    assert len(ev) == 33 and all(e["extractor_match"] for e in ev)
    fields = db.extraction_fields(db.get_extraction("bill_001"))
    assert fields["account_id"] == db.get_truth("bill_001")["account_id"] and fields["net_payable"] == db.get_truth("bill_001")["net_payable"]
    assert services.judge_bill("bill_001")["ok"]
    ev = db.eval_for("bill_001")
    assert all(e["judge_verdict"] == "correct" and e["judge_agrees_with_truth"] for e in ev)
    runs = db.all_runs()
    assert [r["role"] for r in runs] == ["extractor", "judge"] and runs[0]["input_tokens"] == 1200 and runs[1]["output_tokens"] == 400


def test_wrong_extraction_flagged_and_judge_blind_spot_measured(fresh_db):
    ex, jd = FakeExtractor({"net_payable": 1.0, "rr_number": "WRONG"}), FakeJudge(blind=("rr_number",))
    ex.bill_id = jd.bill_id = "bill_002"
    services.set_clients(ex, jd)
    services.scan_bill("bill_002"); services.judge_bill("bill_002")
    ev = {e["field"]: e for e in db.eval_for("bill_002")}
    assert not ev["net_payable"]["extractor_match"] and not ev["rr_number"]["extractor_match"]
    assert ev["net_payable"]["judge_agrees_with_truth"] is True        # judge caught it
    assert ev["rr_number"]["judge_agrees_with_truth"] is False         # judge missed it
    from app import metrics
    j = metrics.judge_quality(db.all_eval())
    assert j["true_errors"] == 2 and j["judge_caught"] == 1 and j["judge_missed"] == 1 and j["recall_pct"] == 50.0
    acc = metrics.extraction_accuracy(db.all_eval(), {"bill_002": db.extraction_fields(db.get_extraction("bill_002"))})
    assert acc["bill_level_pct"] == 0.0 and acc["arithmetic_consistent_pct"] == 0.0


def test_rescan_overwrites_extraction_but_keeps_call_log(fresh_db):
    ex = FakeExtractor({"tax": 0.0}); ex.bill_id = "bill_003"
    services.set_clients(ex, FakeJudge("bill_003"))
    services.scan_bill("bill_003")
    assert db.one("SELECT tax FROM extractions WHERE bill_id='bill_003'")["tax"] == 0
    ex.perturb = {}
    services.scan_bill("bill_003")
    assert db.one("SELECT count(*) AS n FROM extractions WHERE bill_id='bill_003'")["n"] == 1
    assert float(db.one("SELECT tax FROM extractions WHERE bill_id='bill_003'")["tax"]) == db.get_truth("bill_003")["tax"]
    assert all(e["extractor_match"] for e in db.eval_for("bill_003"))
    assert db.one("SELECT count(*) AS n FROM extraction_runs WHERE bill_id='bill_003'")["n"] == 2
    services.judge_bill("bill_003")
    services.scan_bill("bill_003")                                      # re-scan clears stale judge verdicts
    assert all(e["judge_verdict"] is None for e in db.eval_for("bill_003"))


def test_failed_extraction_keeps_state_and_logs(fresh_db):
    ex = FakeExtractor(status="rate_limited"); ex.bill_id = "bill_004"
    services.set_clients(ex, None)
    r = services.scan_bill("bill_004")
    assert not r["ok"] and db.get_bill("bill_004")["scan_status"] == "not_scanned" and db.get_extraction("bill_004") is None
    run = db.all_runs()[0]
    assert run["status"] == "rate_limited" and run["retries"] == 1
    assert services.judge_bill("bill_004")["ok"] is False


def test_read_only_mode_blocks_writes(fresh_db, monkeypatch):
    monkeypatch.setenv("APP_MODE", "local")
    ex = FakeExtractor(); ex.bill_id = "bill_001"
    services.set_clients(ex, FakeJudge("bill_001"))
    with pytest.raises(services.ReadOnlyError):
        services.scan_bill("bill_001")
    with pytest.raises(services.ReadOnlyError):
        services.judge_bill("bill_001")
    assert ex.calls == 0 and db.get_extraction("bill_001") is None
    assert seed.seed_if_empty() is False


def test_db_layer_refuses_writes_in_read_only_mode(fresh_db, monkeypatch):
    monkeypatch.setenv("APP_MODE", "local")
    from app.clients import CallResult
    from app.config import ReadOnlyError
    with pytest.raises(ReadOnlyError):
        db.log_run("bill_001", "extractor", "m", CallResult("ok"))
    with pytest.raises(ReadOnlyError):
        db.save_extraction("bill_001", {}, "m", [])
    with pytest.raises(ReadOnlyError):
        db.save_judge("bill_001", {})
    with pytest.raises(ReadOnlyError):
        db.init_schema()


def test_judge_sees_raw_extraction_and_omitted_fields_are_missing(fresh_db):
    seen = {}

    class Jd:
        def run(self, image, extracted, mime="image/jpeg"):
            seen["extracted"] = extracted
            from app.clients import CallResult
            return CallResult("ok", {"verdicts": {"net_payable": "incorrect", "tax": "garbage"}}, 100, 10, 50, 0)

    ex = FakeExtractor({"net_payable": "Rs 1,0.5.5"}); ex.bill_id = "bill_001"      # unparseable number
    services.set_clients(ex, Jd())
    services.scan_bill("bill_001"); services.judge_bill("bill_001")
    assert seen["extracted"]["net_payable"] == "Rs 1,0.5.5"                           # raw value, not the stored NULL
    ev = {e["field"]: e for e in db.eval_for("bill_001")}
    assert ev["net_payable"]["judge_verdict"] == "incorrect" and ev["net_payable"]["judge_agrees_with_truth"] is True
    assert ev["tax"]["judge_verdict"] == "missing" and ev["tax"]["judge_agrees_with_truth"] is None   # tax was right
    assert ev["tariff"]["judge_verdict"] == "missing"


def test_unexpected_provider_exception_is_logged_as_error(fresh_db):
    class Boom:
        def run(self, *a, **k):
            raise ZeroDivisionError("kaput")
    services.set_clients(Boom(), None)
    r = services.scan_bill("bill_001")
    assert not r["ok"] and "kaput" in r["message"]
    run = db.all_runs()[0]
    assert run["status"] == "error" and "ZeroDivisionError" in run["error"]


def test_repeat_scan_is_refused_while_in_flight_and_after_slot_taken(fresh_db):
    import threading
    started, release = threading.Event(), threading.Event()

    class Slow:
        def run(self, image, mime="image/jpeg"):
            started.set(); release.wait(5)
            return FakeExtractor.run(ex, image, mime)

    ex = FakeExtractor(); ex.bill_id = "bill_001"
    services.set_clients(Slow(), None)
    out = {}
    t = threading.Thread(target=lambda: out.update(services.scan_bill("bill_001")))
    t.start(); assert started.wait(5)
    again = services.scan_bill("bill_001")                       # same bill: refused
    other = services.scan_bill("bill_002")                       # another bill: only one provider call at a time
    release.set(); t.join(5)
    assert not again["ok"] and "already" in again["message"]
    assert not other["ok"] and "Another bill" in other["message"]
    assert out["ok"] and db.one("SELECT count(*) AS n FROM extraction_runs")["n"] == 1


def test_daily_cap_blocks_new_scans(fresh_db, monkeypatch):
    monkeypatch.setenv("GEMINI_DAILY_CAP", "2")
    ex = FakeExtractor(); ex.bill_id = "bill_001"
    services.set_clients(ex, None)
    assert services.scan_bill("bill_001")["ok"] and services.scan_bill("bill_001")["ok"]
    r = services.scan_bill("bill_001")
    assert not r["ok"] and "cap" in r["message"] and ex.calls == 2


def test_cache_is_cleared_by_writes_and_csv_export_has_all_fields(fresh_db):
    import csv, io
    from app import export, fields
    assert db.bills()[0]["scan_status"] != "scanned" and db.extracted_rows() == []
    ex = FakeExtractor({"net_payable": 5.0}); ex.bill_id = "bill_001"
    services.set_clients(ex, FakeJudge("bill_001"))
    services.scan_bill("bill_001")
    b = next(r for r in db.bills() if r["bill_id"] == "bill_001")          # served fresh, not stale
    assert b["scan_status"] == "scanned" and b["extracted_period_end"]
    rows = db.extracted_rows()
    assert len(rows) == 1 and "raw_json" not in rows[0]                    # lean list query
    out = list(csv.reader(io.StringIO(export.extracted_csv(rows, ["bill_001", "bill_002"]))))
    assert out[0] == export.HEADER and len(out[0]) == 3 + len(fields.KEYS) == 36
    assert len(out) == 2                                                   # header + only the selected scanned bill
    d = dict(zip(out[0], out[1]))
    assert d["Net payable"] == "5" and d["Property name"] and d["City"] and d["Owner"]
    assert d["Account ID"] == db.get_truth("bill_001")["account_id"]
