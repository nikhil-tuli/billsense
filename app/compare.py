"""Field normalisation, extractor-vs-truth comparison and arithmetic consistency checks."""
import re
from app.fields import KEYS, TYPES


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    raw = str(v).strip()
    neg = raw.startswith("(") and raw.endswith(")")
    raw = re.sub(r"(?i)\b(rs|inr)\b\.?|\u20b9|/-", "", raw.replace(",", ""))
    s = re.sub(r"[^\d.\-]", "", raw)
    try:
        return -abs(float(s)) if neg else float(s)
    except ValueError:
        return None


def _text(v):
    return re.sub(r"[^0-9a-z./+\-]", "", str(v).casefold())


def match(field: str, truth, extracted) -> bool:
    """True if the extracted value equals the ground truth for this field."""
    if extracted is None or truth is None:
        return extracted is None and truth is None
    if TYPES[field] in (int, float):
        a, b = _num(truth), _num(extracted)
        return a is not None and b is not None and abs(a - b) < 0.005
    return _text(truth) == _text(extracted)


def compare(truth: dict, extracted: dict) -> list[dict]:
    """One row per field: values as text plus whether they match."""
    rows = []
    for k in KEYS:
        t, e = truth.get(k), extracted.get(k)
        rows.append({"field": k, "truth": None if t is None else str(t),
                     "extracted": None if e is None else str(e), "extractor_match": match(k, t, e)})
    return rows


def arithmetic_consistent(f: dict):
    """True/False if the extracted values satisfy the bill's own arithmetic (9% tax as printed on these bills);
    None when a needed value is missing, so the bill cannot be checked."""
    g = {k: _num(f.get(k)) for k in ("units_consumed", "present_reading", "previous_reading", "energy_charges",
                                      "energy_charges_rate", "fixed_charges", "fppca", "pg_surcharge", "tax",
                                      "interest", "debit_credit", "arrears", "current_demand", "net_payable")}
    if any(v is None for v in g.values()):
        return None
    ok = abs(g["present_reading"] - g["previous_reading"] - g["units_consumed"]) < 0.5
    ok &= abs(g["units_consumed"] * g["energy_charges_rate"] - g["energy_charges"]) < 0.02
    ok &= abs((g["fixed_charges"] + g["energy_charges"] + g["fppca"]) * 0.09 - g["tax"]) < 0.02
    cur = g["fixed_charges"] + g["energy_charges"] + g["fppca"] + g["pg_surcharge"] + g["tax"] + g["interest"]
    ok &= abs(cur - g["current_demand"]) < 0.02
    ok &= abs(g["current_demand"] + g["debit_credit"] + g["arrears"] - g["net_payable"]) < 0.02
    return bool(ok)
