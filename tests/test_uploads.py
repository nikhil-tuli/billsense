import io
from datetime import date

import pymupdf
import pytest
from PIL import Image, ImageFilter

from app import db, metrics, services, uploads
from app.config import ReadOnlyError
from tests.conftest import SEED, FakeExtractor, FakeJudge


def sample_jpg(i=1) -> bytes:
    return (SEED / "bills" / f"bill_{i:03d}.jpg").read_bytes()


def to_bytes(img, fmt="JPEG") -> bytes:
    b = io.BytesIO()
    img.save(b, fmt)
    return b.getvalue()


def job(data, name="bill.jpg", city="Pune", prop="Green Park 2", owner="R Iyer", month=date(2026, 10, 1), **kw):
    return uploads.Job(city, prop, owner, month, name, data, **kw)


# ---- content checks (no database needed)
def test_sniff_uses_content_not_extension():
    assert uploads.sniff(sample_jpg()) == "jpeg"
    assert uploads.sniff(to_bytes(Image.new("RGB", (5, 5)), "PNG")) == "png"
    assert uploads.sniff(b"%PDF-1.4 rest") == "pdf"
    for bad in (b"MZ\x90\x00 not an image", b"", b"plain text"):
        with pytest.raises(uploads.Rejected):
            uploads.sniff(bad)


def test_corrupt_and_protected_files_are_rejected():
    with pytest.raises(uploads.Rejected, match="corrupt"):
        uploads.open_image(b"\xff\xd8\xff" + b"garbage" * 50, "jpeg")
    with pytest.raises(uploads.Rejected, match="valid PDF"):
        uploads.open_image(b"%PDF-1.4 broken", "pdf")
    doc = pymupdf.open()
    doc.new_page()
    protected = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="x", user_pw="y")
    with pytest.raises(uploads.Rejected, match="password"):
        uploads.open_image(protected, "pdf")


def test_pdf_first_page_only_is_rendered():
    pages = [Image.open(io.BytesIO(sample_jpg(i))).convert("RGB") for i in (1, 4)]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    img, stored, mime = uploads.open_image(buf.getvalue(), "pdf")
    assert mime == "image/jpeg" and stored[:3] == b"\xff\xd8\xff"
    assert img.width >= 600 and img.width / img.height == pytest.approx(600 / 1434, rel=0.02)   # page 1 shape
    uploads.check_quality(img)


def test_quality_gate_resolution_and_blur():
    good = Image.open(io.BytesIO(sample_jpg()))
    short, score = uploads.check_quality(good)
    assert short == 600 and score > 100
    with pytest.raises(uploads.Rejected, match="blurry"):
        uploads.check_quality(good.filter(ImageFilter.GaussianBlur(3)))
    with pytest.raises(uploads.Rejected, match="Resolution too low"):
        uploads.check_quality(good.resize((300, 717)))
    with pytest.raises(uploads.Rejected, match="blurry"):
        uploads.check_quality(Image.new("RGB", (800, 1200), "white"))
    noisy = Image.open(io.BytesIO(sample_jpg(2)))                      # noisiest sample bill still passes
    uploads.check_quality(noisy)


def test_oversize_and_empty_files_rejected_without_database():
    for data, text in ((b"", "empty"), (b"x" * (uploads.MAX_BYTES + 1), "2 MB")):
        j = job(data)
        with pytest.raises(uploads.Rejected, match=text):
            j._store()


# ---- with the database
def test_successful_upload_creates_bill_and_history(fresh_db):
    r = job(sample_jpg(), name="../x/bill_oct.jpg").run(on_stage=lambda i, l: None)
    assert r["ok"] and r["bill_id"] == "upl_001"
    b = db.get_bill("upl_001")
    assert b["source"] == "upload" and b["scan_status"] == "not_scanned" and b["city"] == "Pune"
    assert (b["bill_period_start"], b["bill_period_end"]) == (date(2026, 10, 1), date(2026, 10, 31))
    assert db.get_image("upl_001")["mime"] == "image/jpeg"
    h = db.uploads_list()[0]
    assert h["status"] == "success" and h["file_name"] == "bill_oct.jpg" and h["bill_id"] == "upl_001"
    assert db.get_upload(h["upload_id"])["file"] is None                 # kept only for non-success rows
    assert next(p for p in db.properties() if p["property_name"] == "Green Park 2")["account_id"].startswith("UPL-")
    assert job(sample_jpg(), prop="Lotus 9", month=date(2026, 11, 1)).run()["bill_id"] == "upl_002"


def test_rejected_file_is_kept_and_retry_works(fresh_db, monkeypatch):
    blurry = to_bytes(Image.open(io.BytesIO(sample_jpg())).filter(ImageFilter.GaussianBlur(3)))
    r = job(blurry).run()
    assert not r["ok"] and "blurry" in r["message"]
    row = db.get_upload(r["upload_id"])
    assert row["status"] == "failure" and "blurry" in row["reason"] and bytes(row["file"]) == blurry
    assert db.bills()[-1]["bill_id"] != "upl_001"                        # no bill was created
    monkeypatch.setenv("UPLOAD_MIN_BLUR_SCORE", "0")                     # e.g. thresholds changed, or a technical failure
    r2 = uploads.Job.from_upload(r["upload_id"]).run()
    assert r2["ok"] and r2["upload_id"] == r["upload_id"]
    assert db.get_upload(r["upload_id"])["status"] == "success"
    assert len(db.uploads_list()) == 1                                   # same history row, not a new one


def test_technical_failure_is_recorded_with_reason(fresh_db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    with monkeypatch.context() as m:
        m.setattr(db, "save_uploaded_bill", boom)
        r = job(sample_jpg()).run()
    assert not r["ok"] and r["message"].startswith("Technical error")
    assert db.get_upload(r["upload_id"])["status"] == "failure"
    assert uploads.Job.from_upload(r["upload_id"]).run()["ok"]


def test_duplicate_needs_replace_and_marks_old_row_replaced(fresh_db):
    first = job(sample_jpg(1)).run()
    dup = uploads.find_duplicate(" pune ", "GREEN  park 2", "r iyer", date(2026, 10, 15))   # case/space-insensitive
    assert dup and dup["bill_id"] == first["bill_id"] and dup["source"] == "upload"
    r = job(sample_jpg(4)).run()
    assert not r["ok"] and "already exists" in r["message"]
    # scan the bill, then replace: extraction is cleared and the bill is Not scanned again
    ex = FakeExtractor(); ex.bill_id = "bill_001"
    services.set_clients(ex, FakeJudge("bill_001"))
    assert services.scan_bill("upl_001")["ok"]
    r = job(sample_jpg(4), replace=True).run()
    assert r["ok"] and r["bill_id"] == "upl_001"
    assert db.get_extraction("upl_001") is None and db.get_bill("upl_001")["scan_status"] == "not_scanned"
    assert db.q("SELECT count(*) AS n FROM bills WHERE source='upload'")[0]["n"] == 1
    statuses = {h["upload_id"]: h["status"] for h in db.uploads_list()}
    assert statuses[first["upload_id"]] == "replaced" and statuses[r["upload_id"]] == "success"


def test_replacing_a_sample_bill_drops_it_from_ground_truth_metrics(fresh_db):
    sb = db.get_bill("bill_001")
    month = sb["bill_period_end"].replace(day=1)
    dup = uploads.find_duplicate(sb["city"], sb["property_name"], sb["owner"], month)
    assert dup["bill_id"] == "bill_001" and dup["source"] == "sample"
    ex = FakeExtractor(); ex.bill_id = "bill_001"
    services.set_clients(ex, FakeJudge("bill_001"))
    services.scan_bill("bill_001")
    assert {r["bill_id"] for r in db.all_eval()} == {"bill_001"}
    r = job(sample_jpg(2), city=sb["city"], prop=sb["property_name"], owner=sb["owner"], month=month, replace=True).run()
    assert r["ok"] and r["bill_id"] == "bill_001"
    b = db.get_bill("bill_001")
    assert b["source"] == "upload" and b["noise_level"] is None and b["scan_status"] == "not_scanned"
    assert db.all_eval() == []


def test_scan_and_judge_an_uploaded_bill_without_ground_truth(fresh_db):
    from app import ui
    job(sample_jpg()).run()
    ex = FakeExtractor({"tax": None, "net_payable": 5.0}); ex.bill_id = "bill_001"
    services.set_clients(ex, FakeJudge("bill_001"))
    assert services.scan_bill("upl_001")["ok"]
    rows = db.eval_rows_all()
    assert len(rows) == 33 and all(r["extractor_match"] is None and r["truth"] is None for r in rows)
    assert db.all_eval() == []                                           # not in ground-truth metrics
    flag = next(b for b in ui._bill_rows() if b["bill_id"] == "upl_001")
    assert flag["flag"] == "Unclear: 1 of 11 missing" and flag["reading_period"]
    assert services.judge_bill("upl_001")["ok"]
    rows = db.eval_rows_all()
    assert all(r["judge_verdict"] and r["judge_agrees_with_truth"] is None for r in rows)
    fields = {"upl_001": db.extraction_fields(db.get_extraction("upl_001"))}
    u = metrics.upload_summary(rows, fields)
    assert u["bills_scanned"] == 1 and u["bills_judged"] == 1 and u["critical_complete_pct"] == 0.0
    assert u["fields_judged_correct_pct"] is not None and u["bills_critical_all_correct_pct"] == 0.0


def test_stale_pending_uploads_become_failure(fresh_db):
    uid = db.upload_insert({"city": "A", "property": "B", "owner": "C", "month": date(2026, 10, 1), "file_name": "f.jpg"},
                           None, 3, b"abc")
    assert db.fail_stale_uploads(10) == 0                                # fresh row is left alone
    db.q("UPDATE uploads SET uploaded_at = now() - interval '11 minutes' WHERE upload_id=%s RETURNING 1", (uid,))
    assert db.fail_stale_uploads(10) == 1
    assert db.get_upload(uid)["status"] == "failure" and "Interrupted" in db.get_upload(uid)["reason"]


def test_one_upload_at_a_time_and_read_only(fresh_db, monkeypatch):
    with uploads.exclusive():
        with pytest.raises(uploads.Busy):
            with uploads.exclusive():
                pass
    monkeypatch.setenv("APP_MODE", "local")
    with pytest.raises(ReadOnlyError):
        db.upload_insert({"city": "A", "property": "B", "owner": "C", "month": date(2026, 10, 1), "file_name": "f"}, None, 1, b"x")
