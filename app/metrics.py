"""Evaluation metrics, computed from the run log and eval_results rows (pure functions)."""
import math
import statistics
from collections import defaultdict

from app import compare
from app.fields import CRITICAL


def pct(x, n):
    return None if not n else round(100.0 * x / n, 1)


def percentile(vals, p):
    """Nearest-rank percentile."""
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def latency_stats(runs):
    ms = [r["latency_ms"] for r in runs if r.get("latency_ms") is not None and r["status"] in ("ok", "invalid_json")]
    if not ms:
        return {"n": 0, "avg": None, "median": None, "p90": None, "p95": None, "max": None}
    return {"n": len(ms), "avg": round(statistics.mean(ms)), "median": round(statistics.median(ms)),
            "p90": percentile(ms, 90), "p95": percentile(ms, 95), "max": max(ms)}


def usage_stats(runs, price_in=None, price_out=None):
    tin = sum(r["input_tokens"] or 0 for r in runs)
    tout = sum(r["output_tokens"] or 0 for r in runs)
    n = len(runs)
    cost = None if price_in is None or price_out is None else round(tin / 1e6 * price_in + tout / 1e6 * price_out, 4)
    return {"calls": n, "input_tokens": tin, "output_tokens": tout,
            "avg_input": round(tin / n) if n else None, "avg_output": round(tout / n) if n else None,
            "equivalent_cost_usd": cost}


def reliability_stats(runs):
    n = len(runs)
    by = defaultdict(int)
    for r in runs:
        by[r["status"]] += 1
    got = by["ok"] + by["invalid_json"]
    return {"calls": n, "ok": by["ok"], "invalid_json": by["invalid_json"], "rate_limited": by["rate_limited"],
            "errors": by["error"], "calls_with_retry": sum(1 for r in runs if r["retries"] > 0),
            "retry_rate_pct": pct(sum(1 for r in runs if r["retries"] > 0), n),
            "error_rate_pct": pct(by["error"] + by["rate_limited"], n), "valid_json_rate_pct": pct(by["ok"], got)}


def extraction_accuracy(eval_rows, extraction_field_dicts):
    """eval_rows: eval_results rows (all bills with an extraction). extraction_field_dicts: bill_id -> fields."""
    n = len(eval_rows)
    per_field = defaultdict(lambda: [0, 0])
    per_bill = defaultdict(list)
    for r in eval_rows:
        per_field[r["field"]][0] += r["extractor_match"]
        per_field[r["field"]][1] += 1
        per_bill[r["bill_id"]].append(r)
    crit = [r for r in eval_rows if r["field"] in CRITICAL]
    bill_ok = sum(1 for rows in per_bill.values() if all(x["extractor_match"] for x in rows if x["field"] in CRITICAL))
    checks = [compare.arithmetic_consistent(f) for f in extraction_field_dicts.values()]
    checkable = [c for c in checks if c is not None]
    return {
        "bills": len(per_bill),
        "all_fields_pct": pct(sum(r["extractor_match"] for r in eval_rows), n),
        "critical_fields_pct": pct(sum(r["extractor_match"] for r in crit), len(crit)),
        "bill_level_pct": pct(bill_ok, len(per_bill)),
        "arithmetic_consistent_pct": pct(sum(checkable), len(checkable)),
        "arithmetic_not_checkable": len(checks) - len(checkable),
        "per_field": {k: pct(v[0], v[1]) for k, v in sorted(per_field.items())},
    }


def judge_quality(eval_rows):
    """Positive class = "the extracted field is wrong". A field the judge omitted ('missing') counts as a miss
    when the field is truly wrong, and is otherwise left out of agreement/precision."""
    judged = [r for r in eval_rows if r["judge_verdict"] is not None]
    if not judged:
        return {"fields_judged": 0}
    wrong = [r for r in judged if not r["extractor_match"]]
    tp = sum(1 for r in wrong if r["judge_verdict"] == "incorrect")
    fn = len(wrong) - tp
    fp = sum(1 for r in judged if r["extractor_match"] and r["judge_verdict"] == "incorrect")
    scored = [r for r in judged if r["judge_agrees_with_truth"] is not None]
    def n(wrong_flag, verdict):
        return sum(1 for r in judged if (not r["extractor_match"]) == wrong_flag and r["judge_verdict"] == verdict)
    matrix = {"extractor_wrong": {"judge_incorrect": n(True, "incorrect"), "judge_correct": n(True, "correct"), "no_verdict": n(True, "missing")},
              "extractor_right": {"judge_incorrect": n(False, "incorrect"), "judge_correct": n(False, "correct"), "no_verdict": n(False, "missing")}}
    return {
        "matrix": matrix,
        "fields_judged": len(judged), "bills_judged": len({r["bill_id"] for r in judged}),
        "fields_without_verdict": sum(1 for r in judged if r["judge_verdict"] == "missing"),
        "agreement_pct": pct(sum(1 for r in scored if r["judge_agrees_with_truth"]), len(scored)),
        "true_errors": len(wrong), "judge_caught": tp, "judge_missed": fn, "judge_false_alarms": fp,
        "precision_pct": pct(tp, tp + fp), "recall_pct": pct(tp, tp + fn),
        "disagreements": [{"bill_id": r["bill_id"], "field": r["field"], "truth": r["truth"],
                           "extracted": r["extracted"], "judge": r["judge_verdict"]}
                          for r in scored if not r["judge_agrees_with_truth"]],
    }


def by_noise(eval_rows, runs, field_dicts=None):
    """Every metric family split by clean / medium / noisy."""
    field_dicts = field_dicts or {}
    out = {}
    for lv in ("clean", "medium", "noisy"):
        rows = [r for r in eval_rows if r["noise_level"] == lv]
        crit = [r for r in rows if r["field"] in CRITICAL]
        bills = defaultdict(list)
        for r in rows:
            bills[r["bill_id"]].append(r)
        bill_ok = sum(1 for rs in bills.values() if all(x["extractor_match"] for x in rs if x["field"] in CRITICAL))
        checks = [compare.arithmetic_consistent(field_dicts[b]) for b in bills if b in field_dicts]
        checkable = [c for c in checks if c is not None]
        ex = [r for r in runs if r["role"] == "extractor" and r["noise_level"] == lv]
        jd = [r for r in runs if r["role"] == "judge" and r["noise_level"] == lv]
        j = judge_quality(rows)
        eu, ju = usage_stats(ex), usage_stats(jd)
        out[lv] = {
            "bills": len(bills),
            "all_fields_pct": pct(sum(r["extractor_match"] for r in rows), len(rows)),
            "critical_fields_pct": pct(sum(r["extractor_match"] for r in crit), len(crit)),
            "bill_level_pct": pct(bill_ok, len(bills)),
            "arithmetic_consistent_pct": pct(sum(checkable), len(checkable)),
            "extractor_calls": eu["calls"], "extractor_avg_in_tokens": eu["avg_input"], "extractor_avg_out_tokens": eu["avg_output"],
            "extractor_latency_avg_ms": latency_stats(ex)["avg"], "extractor_latency_median_ms": latency_stats(ex)["median"],
            "extractor_latency_p90_ms": latency_stats(ex)["p90"], "extractor_error_rate_pct": reliability_stats(ex)["error_rate_pct"],
            "judge_calls": ju["calls"], "judge_avg_in_tokens": ju["avg_input"], "judge_avg_out_tokens": ju["avg_output"],
            "judge_latency_avg_ms": latency_stats(jd)["avg"], "judge_latency_p90_ms": latency_stats(jd)["p90"],
            "judge_error_rate_pct": reliability_stats(jd)["error_rate_pct"], "judge_agreement_pct": j.get("agreement_pct"),
        }
    return out


def per_bill(eval_rows, runs):
    """One summary row per evaluated bill."""
    by = defaultdict(list)
    for r in eval_rows:
        by[r["bill_id"]].append(r)
    rows = []
    for bid, rs in sorted(by.items()):
        crit = [r for r in rs if r["field"] in CRITICAL]
        scored = [r for r in rs if r["judge_agrees_with_truth"] is not None]
        last = lambda role: next((x["latency_ms"] for x in reversed(runs) if x["bill_id"] == bid and x["role"] == role and x["status"] == "ok"), None)
        rows.append({"bill_id": bid, "noise": rs[0]["noise_level"],
                     "critical_correct": f"{sum(r['extractor_match'] for r in crit)}/{len(crit)}",
                     "fields_correct": f"{sum(r['extractor_match'] for r in rs)}/{len(rs)}",
                     "judge_agreement_pct": pct(sum(1 for r in scored if r["judge_agrees_with_truth"]), len(scored)) if scored else None,
                     "extractor_latency_ms": last("extractor"), "judge_latency_ms": last("judge")})
    return rows


def mismatches(eval_rows):
    """Every field where the extraction differs from ground truth, with the judge's verdict."""
    return [{"bill_id": r["bill_id"], "field": r["field"], "truth": r["truth"], "extracted": r["extracted"],
             "judge_verdict": r["judge_verdict"] or "not judged"} for r in eval_rows if not r["extractor_match"]]


def summarize(runs, eval_rows, extraction_field_dicts, prices=None):
    prices = prices or {}
    ext = [r for r in runs if r["role"] == "extractor"]
    jud = [r for r in runs if r["role"] == "judge"]
    return {
        "usage": {"extractor": usage_stats(ext, *prices.get("extractor", (None, None))),
                  "judge": usage_stats(jud, *prices.get("judge", (None, None)))},
        "latency_ms": {"extractor": latency_stats(ext), "judge": latency_stats(jud)},
        "reliability": {"extractor": reliability_stats(ext), "judge": reliability_stats(jud)},
        "accuracy": extraction_accuracy(eval_rows, extraction_field_dicts),
        "judge_quality": judge_quality(eval_rows),
        "by_noise": by_noise(eval_rows, runs, extraction_field_dicts),
        "per_bill": per_bill(eval_rows, runs),
        "mismatches": mismatches(eval_rows),
    }


def upload_summary(upload_eval_rows, upload_field_dicts):
    """Judge-estimated quality of uploaded bills (no ground truth). upload_field_dicts: bill_id -> extracted fields."""
    from app.fields import PRIMARY
    by_bill = {}
    for r in upload_eval_rows:
        by_bill.setdefault(r["bill_id"], {})[r["field"]] = r["judge_verdict"]
    judged = {b: v for b, v in by_bill.items() if any(x is not None for x in v.values())}
    verdicts = [x for v in judged.values() for x in v.values()]
    n_correct = sum(x == "correct" for x in verdicts)
    n_incorrect = sum(x == "incorrect" for x in verdicts)
    n_missing = sum(x == "missing" for x in verdicts)
    complete = sum(all(f.get(k) not in (None, "") for k in PRIMARY) for f in upload_field_dicts.values())
    all_ok = sum(all(v.get(k) == "correct" for k in PRIMARY) for v in judged.values())
    return {"bills_scanned": len(upload_field_dicts), "bills_judged": len(judged),
            "fields_judged_correct_pct": pct(n_correct, n_correct + n_incorrect),
            "bills_critical_all_correct_pct": pct(all_ok, len(judged)),
            "critical_complete_pct": pct(complete, len(upload_field_dicts)),
            "fields_without_verdict": n_missing}
