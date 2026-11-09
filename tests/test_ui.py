import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

from app import cascade, db, services
from tests.conftest import FakeExtractor, FakeJudge


def selects(user):
    """Bills-tab filters by label (the Upload tab has selects with the same labels; Bills ones are created first)."""
    out = {}
    for s in sorted(user.find(kind=ui.select).elements, key=lambda e: e.id):
        out.setdefault(s.props.get("label"), s)
    return out


def bills_table(user):
    return [t for t in user.find(kind=ui.table).elements if any(c["name"] == "period" for c in t.columns)][0]


def names(user):
    return sorted(r["property"] for r in bills_table(user).rows)


def emit(element, event_type, args):
    """Fire a client-side event (e.g. the Scan button's custom event) on a NiceGUI element."""
    lid = next(k for k, l in element._event_listeners.items() if l.type == event_type)
    element._handle_event({"listener_id": lid, "args": args})


@pytest.mark.usefixtures("fresh_db")
async def test_filters_cascade_in_ui(user: User):
    services.set_clients(FakeExtractor(), FakeJudge())
    await user.open("/")
    props = db.properties()
    assert len(names(user)) == 100 and "Lakeview Apartments 4B" in names(user)
    s = selects(user)
    mumbai = sorted(p["property_name"] for p in props if p["city"] == "Mumbai")
    s["City"].set_value("Mumbai")
    await asyncio.sleep(0.5)
    assert sorted(names(user)) == mumbai and len(mumbai) == 10
    assert set(s["Owner"].options) == {p["owner"] for p in props if p["city"] == "Mumbai"}
    ravi_mumbai = sorted(p["property_name"] for p in props if p["city"] == "Mumbai" and p["owner"] == "Ravi Kumar")
    s["Owner"].set_value("Ravi Kumar")
    await asyncio.sleep(0.5)
    assert sorted(s["Property name"].options) == ravi_mumbai and sorted(names(user)) == ravi_mumbai
    s["Owner"].set_value(None)                      # clearing Owner re-widens the other two
    await asyncio.sleep(0.5)
    assert len(s["Property name"].options) == 10
    s["City"].set_value(None)
    await asyncio.sleep(0.5)
    assert len(names(user)) == 100 and len(s["City"].options) == 10
    # start from Owner instead: Ravi Kumar owns properties in several cities
    ravi = [p for p in props if p["owner"] == "Ravi Kumar"]
    s["Owner"].set_value("Ravi Kumar")
    await asyncio.sleep(0.5)
    assert s["City"].options == sorted({p["city"] for p in ravi}) and sorted(names(user)) == sorted(p["property_name"] for p in ravi)


@pytest.mark.usefixtures("fresh_db")
async def test_scan_fills_account_id_and_detail_rows(user: User):
    bill = db.bills()[0]
    ex = FakeExtractor({"net_payable": 5.0}); ex.bill_id = bill["bill_id"]
    services.set_clients(ex, FakeJudge(bill["bill_id"]))
    await user.open("/")
    assert bills_table(user).columns[0]["name"] == "account_id"           # extreme left
    row = lambda: next(r for r in bills_table(user).rows if r["bill_id"] == bill["bill_id"])
    assert row()["account_id"] == "" and row()["detail"] == [] and row()["status"] == "Not scanned"
    emit(bills_table(user), "scan", bill["bill_id"])
    await user.should_see("Scanned", retries=40)
    await asyncio.sleep(0.5)
    r = row()
    assert r["status"] == "Scanned" and r["account_id"] == db.get_truth(bill["bill_id"])["account_id"]
    assert r["period"] == cascade.month_year(db.get_truth(bill["bill_id"])["bill_period_end"]) != ""   # filled by the scan
    assert [c["label"] for c in bills_table(user).columns if c["name"] == "period"] == ["Bill reading period"]
    assert r["_open"] is True                                     # the scanned bill opens in place
    by = {d["k"]: d for d in r["detail"]}
    assert len(r["detail"]) == 33 and sum(d["p"] for d in r["detail"]) == 11
    assert by["net_payable"]["m"] is False and by["net_payable"]["e"] == "5.0" and by["tax"]["m"] is True
    assert db.get_bill(bill["bill_id"])["scan_status"] == "scanned" and ex.calls == 1


@pytest.mark.usefixtures("fresh_db")
async def test_extracted_tab_and_evaluation_tab(user: User):
    for bid in ("bill_001", "bill_002"):
        ex = FakeExtractor({"net_payable": 5.0} if bid == "bill_002" else {}); ex.bill_id = bid
        services.set_clients(ex, FakeJudge(bid))
        services.scan_bill(bid); services.judge_bill(bid)
    await user.open("/")
    user.find(kind=ui.tab, content="Extracted data").click()
    await user.should_see("Download CSV (0 selected)")
    dt = [t for t in user.find(kind=ui.table).elements if any(c["name"] == "net_payable" for c in t.columns)][0]
    assert len(dt.columns) == 36 and len(dt.rows) == 2 and dt._props["visible-columns"] == ["city", "owner", "property_name"] + __import__("app.fields", fromlist=["PRIMARY"]).PRIMARY
    assert dt._props["selection"] == "multiple"
    user.find(kind=ui.tab, content="Field guide").click()
    await user.should_see("All 33 fields read from each bill")
    gt = [t for t in user.find(kind=ui.table).elements if any(c["name"] == "meaning" for c in t.columns)][0]
    assert len(gt.rows) == 33 and all(r["meaning"] for r in gt.rows) and sum(r["group"] == "Critical" for r in gt.rows) == 11
    user.find(kind=ui.tab, content="Evaluation").click()
    await user.should_see("Extraction accuracy vs ground truth")
    await user.should_see("LLM judge vs ground truth")
    await user.should_see("By noise level")
    await user.should_see("confusion matrix")
    await user.should_see("extractor vs ground truth (%), lowest first")
    await user.should_see("Show all 33 fields")
    await user.should_see("Per-bill results")
    await user.should_see("Field mismatches")
    infos = {r["metric"]: r["_info"] for t in user.find(kind=ui.table).elements for r in t.rows if r.get("_info")}
    assert set(infos) == {"Critical fields match (%)", "Arithmetic-consistent bills (%), of checkable", "fields without verdict"}
    assert "Net payable" in infos["Critical fields match (%)"] and "Due date" in infos["Critical fields match (%)"]


async def test_read_only_banner(user: User, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:54329/billtest")
    monkeypatch.setenv("APP_MODE", "local")
    await user.open("/")
    await user.should_see("Read-only")


@pytest.mark.usefixtures("fresh_db")
async def test_judge_event_runs_judge_for_scanned_bill(user: User, monkeypatch):
    ex, jd = FakeExtractor(), FakeJudge("bill_001")
    ex.bill_id = "bill_001"
    services.set_clients(ex, jd)
    services.scan_bill("bill_001")
    await user.open("/")
    user.find(kind=ui.tab, content="Evaluation").click()
    await asyncio.sleep(0.5)
    table = [t for t in user.find(kind=ui.table).elements if any(c["name"] == "judged" for c in t.columns)][0]
    assert table.rows[0]["judged"] == "no"
    emit(table, "judge", "bill_001")
    await user.should_see("Judged bill_001", retries=40)
    assert jd.calls == 1 and db.judged_bills() == {"bill_001"}


@pytest.mark.usefixtures("fresh_db")
async def test_scan_event_in_read_only_mode_does_nothing(user: User, monkeypatch):
    ex = FakeExtractor(); ex.bill_id = "bill_001"
    services.set_clients(ex, FakeJudge("bill_001"))
    monkeypatch.setenv("APP_MODE", "local")
    await user.open("/")
    emit(bills_table(user), "scan", "bill_001")
    await user.should_see("Read-only mode", retries=20)
    assert ex.calls == 0 and db.get_extraction("bill_001") is None


@pytest.mark.usefixtures("fresh_db")
async def test_upload_tab_is_second_and_history_view_and_retry(user: User, monkeypatch):
    import io
    from datetime import date
    from PIL import Image, ImageFilter
    from app import uploads
    from tests.test_uploads import sample_jpg, to_bytes
    ok = uploads.Job("Pune", "Green Park 2", "R Iyer", date(2026, 10, 1), "good.jpg", sample_jpg()).run()
    blurry = to_bytes(Image.open(io.BytesIO(sample_jpg())).filter(ImageFilter.GaussianBlur(3)))
    bad = uploads.Job("Delhi", "Lotus 9", "S Khan", date(2026, 9, 1), "blurry.jpg", blurry).run()
    assert ok["ok"] and not bad["ok"]
    await user.open("/")
    assert [t.props.get("label") for t in sorted(user.find(kind=ui.tab).elements, key=lambda e: e.id)] == \
        ["Bills", "Upload", "Extracted data", "Evaluation", "Field guide"]
    user.find(kind=ui.tab, content="Upload").click()
    await user.should_see("Don't upload real bills")
    await user.should_see("Upload history")
    hist = [t for t in user.find(kind=ui.table).elements if any(c["name"] == "uploaded" for c in t.columns)][0]
    by_file = {r["file"]: r for r in hist.rows}
    assert by_file["good.jpg"]["status"] == "Success" and by_file["blurry.jpg"]["status"] == "Failure"
    assert "blurry" in by_file["blurry.jpg"]["reason"].lower() and by_file["good.jpg"]["month"] == "Oct 2026"
    emit(hist, "view", by_file["good.jpg"])                               # View jumps to the bill on the Bills tab
    await asyncio.sleep(0.5)
    assert selects(user)["City"].value == "Pune" and selects(user)["Property name"].value == "Green Park 2"
    assert names(user) == ["Green Park 2"]
    assert bills_table(user).rows[0]["_open"] is True and bills_table(user).rows[0]["status"] == "Not scanned"
    monkeypatch.setenv("UPLOAD_MIN_BLUR_SCORE", "0")                      # Retry re-runs the kept file
    emit(hist, "retry", by_file["blurry.jpg"]["upload_id"])
    await user.should_see("Uploaded blurry.jpg", retries=40)
    assert db.get_upload(by_file["blurry.jpg"]["upload_id"])["status"] == "success"
