"""CSV export of extracted bills."""
import csv
import io

from app import db
from app.fields import KEYS, LABELS

HEADER = ["City", "Owner", "Property name"] + [LABELS[k] for k in KEYS]


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def extracted_csv(ext_rows, bill_ids) -> str:
    """CSV with city, owner, property name and all 33 fields for the given bill ids (in table order)."""
    wanted = set(bill_ids)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    for r in ext_rows:
        if r["bill_id"] in wanted:
            f = db.extraction_fields(r)
            w.writerow([r["city"], r["owner"], r["property_name"]] + [_cell(f.get(k)) for k in KEYS])
    return buf.getvalue()
