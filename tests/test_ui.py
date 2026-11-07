import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

from app import db, services
from tests.conftest import FakeExtractor, FakeJudge


def selects(user):
    return {s.props.get("label"): s for s in user.find(kind=ui.select).elements}


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
    assert len(names(user)) == 20 and "Lakeview Apartments 4B" in names(user)
    s = selects(user)
    s["City"].set_value("Mumbai")
    await asyncio.sleep(0.5)
    assert names(user) == ["Andheri Nest", "Bandra Pearl 14", "Powai Lake View 7", "Seaside Heights 12"]
    assert set(s["Owner"].options) == {"Ravi Kumar", "Farhan Sheikh", "Imran Qureshi", "Arjun Reddy"}
    s["Owner"].set_value("Ravi Kumar")
    await asyncio.sleep(0.5)
    assert s["Property name"].options == ["Seaside Heights 12"] and names(user) == ["Seaside Heights 12"]
    s["Owner"].set_value(None)                      # clearing Owner re-widens the other two
    await asyncio.sleep(0.5)
    assert len(s["Property name"].options) == 4
    s["City"].set_value(None)
    await asyncio.sleep(0.5)
    assert len(names(user)) == 20 and len(s["City"].options) == 7
    # start from Owner instead: Ravi Kumar owns properties in Bengaluru and Mumbai
    s["Owner"].set_value("Ravi Kumar")
    await asyncio.sleep(0.5)
    assert s["City"].options == ["Bengaluru", "Mumbai"] and names(user) == ["Lakeview Apartments 4B", "Seaside Heights 12"]


@pytest.mark.usefixtures("fresh_db")
async def test_scan_button_event_scans_and_selects_bill(user: User):
    bill = db.bills()[0]
    ex = FakeExtractor(); ex.bill_id = bill["bill_id"]
    services.set_clients(ex, FakeJudge(bill["bill_id"]))
    await user.open("/")
    await user.should_see("Select a bill")
    table = [t for t in user.find(kind=ui.table).elements if "bill_id" in str(t.props.get("row-key"))][0]
    emit(table, "scan", bill["bill_id"])
    await user.should_see("Scanned", retries=40)
    await asyncio.sleep(0.5)
    assert [r["status"] for r in bills_table(user).rows if r["bill_id"] == bill["bill_id"]] == ["Scanned"]
    assert db.get_bill(bill["bill_id"])["scan_status"] == "scanned" and ex.calls == 1
    await user.should_see("Net payable")          # detail panel shows extracted fields for the scanned bill


@pytest.mark.usefixtures("fresh_db")
async def test_extracted_tab_and_evaluation_tab(user: User):
    for bid in ("bill_001", "bill_002"):
        ex = FakeExtractor({"net_payable": 5.0} if bid == "bill_002" else {}); ex.bill_id = bid
        services.set_clients(ex, FakeJudge(bid))
        services.scan_bill(bid); services.judge_bill(bid)
    await user.open("/")
    user.find(kind=ui.tab, content="Extracted data").click()
    await user.should_see("Property name")
    user.find(kind=ui.tab, content="Evaluation").click()
    await user.should_see("Extraction accuracy vs ground truth")
    await user.should_see("LLM judge vs ground truth")
    await user.should_see("By noise level")
    await user.should_see("Per-bill results")
    await user.should_see("Field mismatches")


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
