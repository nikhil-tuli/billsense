import json
from pathlib import Path

from app import cascade, compare, metrics
from tests.conftest import SEED, truth_of

def _row(city, owner, prop, period="", status="Not scanned"):
    return {"city": city, "owner": owner, "property_name": prop, "reading_period": period, "scan_label": status}


PROPS = [
    _row("Bengaluru", "Ravi", "A"),
    _row("Mumbai", "Ravi", "B"),
    _row("Bengaluru", "Meera", "C"),
    _row("Chennai", "Meera", "D"),
]


def test_match_rules():
    assert compare.match("net_payable", 1663.66, "Rs 1,663.66")
    assert compare.match("net_payable", 1663.66, "1663.660")
    assert not compare.match("net_payable", 1663.66, 1663.65)
    assert compare.match("units_consumed", 182, 182.0)
    assert compare.match("address", "NO 72 SEASIDE, ANDHERI WEST", "no 72 seaside andheri west")
    assert not compare.match("account_id", "1000000002", "1000000003")
    assert compare.match("tax", None, None) and not compare.match("tax", 1.0, None)
    assert compare.match("recorded_md", "0.6 KW", "0.6KW")
    for printed in ("Rs. 1,663.66", "1,663.66/-", "Rs.1663.66", "\u20b9 1663.66"):
        assert compare.match("net_payable", 1663.66, printed), printed
    assert compare.match("debit_credit", -286.61, "(286.61)")
    assert not compare.match("due_date", "05/10/2026", "5/10/2026")        # dates are compared exactly as printed


def test_ground_truth_is_arithmetically_consistent_and_perturbation_is_caught():
    for f in sorted((SEED / "ground_truth").glob("*.json")):
        g = json.loads(f.read_text())
        assert compare.arithmetic_consistent(g), f.name
    g = truth_of("bill_001")
    assert not compare.arithmetic_consistent({**g, "net_payable": g["net_payable"] + 1})
    assert compare.arithmetic_consistent({**g, "tax": None}) is None      # cannot be checked, not "failed"


def test_cascade_each_filter_narrows_others():
    assert cascade.options(PROPS, {})["city"] == ["Bengaluru", "Chennai", "Mumbai"]
    o = cascade.options(PROPS, {"city": "Bengaluru"})
    assert o["owner"] == ["Meera", "Ravi"] and o["property"] == ["A", "C"]
    o = cascade.options(PROPS, {"owner": "Meera"})
    assert o["city"] == ["Bengaluru", "Chennai"] and o["property"] == ["C", "D"]
    o = cascade.options(PROPS, {"property": "B"})
    assert o["city"] == ["Mumbai"] and o["owner"] == ["Ravi"]
    # an invalid leftover selection is dropped
    n = cascade.normalise(PROPS, {"city": "Mumbai", "owner": "Meera"}, keep="city")
    assert n["city"] == "Mumbai" and n["owner"] is None
    n = cascade.normalise(PROPS, {"city": "Bengaluru", "owner": "Meera"}, keep="city")
    assert (n["city"], n["owner"]) == ("Bengaluru", "Meera")
    bills = [{**_row("Mumbai", "Ravi", "B"), "bill_id": 1}, {**_row("Chennai", "Meera", "D"), "bill_id": 2}]
    assert [b["bill_id"] for b in cascade.filter_bills(bills, {"owner": "Ravi"})] == [1]


def test_percentiles_and_latency():
    assert metrics.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == 9
    assert metrics.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    runs = [{"latency_ms": v, "status": "ok"} for v in (100, 200, 300, 400, 1000)]
    s = metrics.latency_stats(runs)
    assert (s["avg"], s["median"], s["p90"], s["max"]) == (400, 300, 1000, 1000)
    assert metrics.latency_stats([])["avg"] is None


def test_judge_quality_counts():
    rows = [  # field truly wrong? judge verdict
        {"bill_id": "b", "field": "f1", "extractor_match": False, "judge_verdict": "incorrect", "judge_agrees_with_truth": True, "truth": "1", "extracted": "2"},
        {"bill_id": "b", "field": "f2", "extractor_match": False, "judge_verdict": "correct", "judge_agrees_with_truth": False, "truth": "1", "extracted": "2"},
        {"bill_id": "b", "field": "f3", "extractor_match": True, "judge_verdict": "incorrect", "judge_agrees_with_truth": False, "truth": "1", "extracted": "1"},
        {"bill_id": "b", "field": "f4", "extractor_match": True, "judge_verdict": "correct", "judge_agrees_with_truth": True, "truth": "1", "extracted": "1"},
    ]
    j = metrics.judge_quality(rows)
    assert (j["agreement_pct"], j["precision_pct"], j["recall_pct"]) == (50.0, 50.0, 50.0)
    assert j["judge_caught"] == 1 and j["judge_missed"] == 1 and j["judge_false_alarms"] == 1
    assert len(j["disagreements"]) == 2
    assert metrics.judge_quality([{"judge_verdict": None}])["fields_judged"] == 0


def test_reliability_and_usage():
    runs = [{"status": "ok", "retries": 0, "input_tokens": 100, "output_tokens": 10},
            {"status": "ok", "retries": 2, "input_tokens": 300, "output_tokens": 30},
            {"status": "invalid_json", "retries": 0, "input_tokens": 50, "output_tokens": 5},
            {"status": "rate_limited", "retries": 3, "input_tokens": None, "output_tokens": None}]
    r = metrics.reliability_stats(runs)
    assert r["valid_json_rate_pct"] == 66.7 and r["retry_rate_pct"] == 50.0 and r["error_rate_pct"] == 25.0
    u = metrics.usage_stats(runs, 1.0, 2.0)
    assert u["input_tokens"] == 450 and u["output_tokens"] == 45 and u["equivalent_cost_usd"] == round(450 / 1e6 + 90 / 1e6, 4)
    assert metrics.usage_stats(runs)["equivalent_cost_usd"] is None


def test_judge_missing_verdicts_count_as_misses_only_for_true_errors():
    rows = [
        {"bill_id": "b", "field": "f1", "extractor_match": False, "judge_verdict": "missing", "judge_agrees_with_truth": False, "truth": "1", "extracted": "2"},
        {"bill_id": "b", "field": "f2", "extractor_match": True, "judge_verdict": "missing", "judge_agrees_with_truth": None, "truth": "1", "extracted": "1"},
        {"bill_id": "b", "field": "f3", "extractor_match": False, "judge_verdict": "incorrect", "judge_agrees_with_truth": True, "truth": "1", "extracted": "2"},
    ]
    j = metrics.judge_quality(rows)
    assert j["fields_without_verdict"] == 2 and j["judge_missed"] == 1 and j["judge_caught"] == 1 and j["recall_pct"] == 50.0
    assert j["agreement_pct"] == 50.0 and len(j["disagreements"]) == 1


def test_per_bill_mismatches_and_noise_split():
    ev = [{"bill_id": "b1", "noise_level": "clean", "field": "net_payable", "extractor_match": False, "truth": "5", "extracted": "6",
           "judge_verdict": "incorrect", "judge_agrees_with_truth": True},
          {"bill_id": "b1", "noise_level": "clean", "field": "tax", "extractor_match": True, "truth": "1", "extracted": "1",
           "judge_verdict": "correct", "judge_agrees_with_truth": True}]
    runs = [{"bill_id": "b1", "role": "extractor", "noise_level": "clean", "status": "ok", "latency_ms": 900, "retries": 0, "input_tokens": 10, "output_tokens": 2}]
    assert metrics.mismatches(ev)[0]["judge_verdict"] == "incorrect"
    pb = metrics.per_bill(ev, runs)[0]
    assert pb["critical_correct"] == "1/2" and pb["extractor_latency_ms"] == 900
    bn = metrics.by_noise(ev, runs)
    assert bn["clean"]["bills"] == 1 and bn["clean"]["extractor_latency_avg_ms"] == 900 and bn["noisy"]["bills"] == 0


def test_judge_confusion_matrix_counts_every_cell():
    def r(match, verdict):
        return {"bill_id": "b", "field": "f", "extractor_match": match, "judge_verdict": verdict,
                "judge_agrees_with_truth": None, "truth": "1", "extracted": "1"}
    rows = [r(False, "incorrect"), r(False, "incorrect"), r(False, "correct"), r(False, "missing"),
            r(True, "incorrect"), r(True, "correct"), r(True, "correct"), r(True, "correct"), r(True, "missing")]
    mx = metrics.judge_quality(rows)["matrix"]
    assert mx["extractor_wrong"] == {"judge_incorrect": 2, "judge_correct": 1, "no_verdict": 1}
    assert mx["extractor_right"] == {"judge_incorrect": 1, "judge_correct": 3, "no_verdict": 1}


def test_month_year_of_bill_period_end():
    assert cascade.month_year("07/11/2026") == "Nov 2026"
    assert cascade.month_year("2026-12-03") == "Dec 2026"
    assert cascade.month_year("03-Jan-2027") == "Jan 2027"
    assert cascade.month_year(None) == "" and cascade.month_year("") == "" and cascade.month_year("garbage") == ""


def test_cascade_reading_period_and_scan_status():
    rows = [_row("Mumbai", "Ravi", "B", "Nov 2026", "Scanned"), _row("Chennai", "Meera", "D", "Jan 2027", "Scanned"),
            _row("Chennai", "Meera", "E", "Dec 2026", "Scanned"), _row("Pune", "Asha", "F")]
    o = cascade.options(rows, {})
    assert o["period"] == ["Nov 2026", "Dec 2026", "Jan 2027"]             # chronological, scanned bills only
    assert o["status"] == ["Not scanned", "Scanned"]
    assert cascade.options(rows, {"city": "Pune"})["period"] == []        # unscanned bills have no period
    assert cascade.options(rows, {"period": "Nov 2026"})["city"] == ["Mumbai"]
    assert cascade.options(rows, {"status": "Not scanned"})["city"] == ["Pune"]
    assert [r["property_name"] for r in cascade.filter_bills(rows, {"period": "Dec 2026"})] == ["E"]
    assert [r["property_name"] for r in cascade.filter_bills(rows, {"status": "Not scanned"})] == ["F"]
    n = cascade.normalise(rows, {"city": "Mumbai", "period": "Jan 2027"}, keep="period")
    assert n["period"] == "Jan 2027" and n["city"] is None
