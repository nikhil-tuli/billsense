"""Cascading Bills-tab filters (City, Owner, Property, Bill reading period, Scan status):
choosing any one narrows the options of all the others."""
from datetime import datetime

# filter name -> key in the bill rows
COLS = {"city": "city", "owner": "owner", "property": "property_name",
        "period": "reading_period", "status": "scan_label"}

_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y",
                 "%d/%m/%y", "%d-%m-%y", "%d-%b-%y", "%b %d, %Y")


def month_year(text) -> str:
    """'07/11/2026' -> 'Nov 2026'. Blank when there is no value or it cannot be read as a date."""
    s = (str(text) if text is not None else "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%b %Y")
        except ValueError:
            continue
    return ""


def _sorted(name, vals):
    if name == "period":                                     # chronological, not alphabetical
        return sorted(vals, key=lambda v: datetime.strptime(v, "%b %Y"))
    return sorted(vals)


def options(rows: list[dict], sel: dict) -> dict:
    """rows: bill rows. sel: {filter name: value or None}.
    Each filter's options come from the rows matching the OTHER selections (blank values are not options)."""
    out = {}
    for name, col in COLS.items():
        vals = set()
        for r in rows:
            if r[col] and all(not sel.get(o) or r[c] == sel[o] for o, c in COLS.items() if o != name):
                vals.add(r[col])
        out[name] = _sorted(name, vals)
    return out


def normalise(rows, sel, keep=None) -> dict:
    """Drop any selected value that is no longer valid. If `keep` (the filter the user just changed) is given,
    it wins: the other filters that conflict with it are dropped."""
    sel = {n: sel.get(n) for n in COLS}
    if keep and sel.get(keep):
        for name, col in COLS.items():
            if name != keep and sel.get(name) and not any(
                    r[COLS[keep]] == sel[keep] and r[col] == sel[name] for r in rows):
                sel[name] = None
    for _ in range(len(COLS)):
        opts = options(rows, sel)
        changed = False
        for name in COLS:
            if sel.get(name) and sel[name] not in opts[name]:
                sel[name] = None
                changed = True
        if not changed:
            break
    return sel


def filter_bills(bills: list[dict], sel: dict) -> list[dict]:
    return [b for b in bills if all(not sel.get(n) or b[c] == sel[n] for n, c in COLS.items())]
