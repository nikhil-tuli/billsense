"""Cascading City / Owner / Property filters: choosing any one narrows the other two."""

COLS = {"city": "city", "owner": "owner", "property": "property_name"}


def options(props: list[dict], sel: dict) -> dict:
    """props: rows with city, owner, property_name. sel: {'city'|'owner'|'property': value or None}.
    Each filter's options come from the properties matching the OTHER two selections."""
    out = {}
    for name, col in COLS.items():
        vals = set()
        for p in props:
            if all(not sel.get(o) or p[c] == sel[o] for o, c in COLS.items() if o != name):
                vals.add(p[col])
        out[name] = sorted(vals)
    return out


def normalise(props, sel, keep=None) -> dict:
    """Drop any selected value that is no longer valid. If `keep` (the filter the user just changed) is given,
    it wins: the other filters that conflict with it are dropped."""
    sel = dict(sel)
    if keep and sel.get(keep):
        for name, col in COLS.items():
            if name != keep and sel.get(name) and not any(
                    p[COLS[keep]] == sel[keep] and p[col] == sel[name] for p in props):
                sel[name] = None
    for _ in range(3):
        opts = options(props, sel)
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
