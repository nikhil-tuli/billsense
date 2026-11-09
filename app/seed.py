"""Top-up loader for the bundled synthetic data (only when writes are enabled).

Append-only: inserts bills, properties and owners that are missing and never updates or deletes
an existing row, so scan results, judge verdicts, run logs and uploads are untouched.
Idempotent: a second call adds nothing.
"""
import json
from datetime import datetime

from app import db
from app.config import get_settings


def _d(s):
    return datetime.strptime(s, "%d/%m/%Y").date()


def seed_missing() -> int:
    """Add any bundled bill that is not in the database yet. Returns the number of bills added."""
    s = get_settings()
    if not s.writes_enabled:
        return 0
    seed = json.loads((s.seed_dir / "seed.json").read_text())
    have = {r["bill_id"] for r in db.q("SELECT bill_id FROM bills")}
    todo = [b for b in seed["bills"] if b["bill_id"] not in have]
    if not todo:
        return 0
    props = {p["account_id"]: p for p in seed["properties"]}
    added = 0
    with db.tx() as c:
        for b in todo:
            p = props[b["account_id"]]
            o = c.execute("SELECT owner_id FROM owners WHERE lower(name)=lower(%s)", (p["owner"],)).fetchone()
            if not o:
                o = c.execute("INSERT INTO owners (name) VALUES (%s) RETURNING owner_id", (p["owner"],)).fetchone()
            if not c.execute("SELECT 1 FROM properties WHERE account_id=%s", (p["account_id"],)).fetchone():
                if c.execute("""SELECT 1 FROM properties WHERE owner_id=%s AND lower(city)=lower(%s)
                                AND lower(property_name)=lower(%s)""",
                             (o["owner_id"], p["city"], p["property_name"])).fetchone():
                    continue                      # an upload already created this property: leave it alone
                # property_id is allocated here, not taken from the seed: uploads also use max+1
                pid = c.execute("SELECT COALESCE(max(property_id),0)+1 AS n FROM properties").fetchone()["n"]
                c.execute("""INSERT INTO properties (property_id, owner_id, account_id, rr_number, property_name, city)
                             VALUES (%s,%s,%s,%s,%s,%s)""",
                          (pid, o["owner_id"], p["account_id"], p["rr_number"], p["property_name"], p["city"]))
            gt = json.loads((s.seed_dir / b["ground_truth"]).read_text())
            c.execute("""INSERT INTO bills (bill_id, account_id, bill_period_start, bill_period_end, noise_level)
                         VALUES (%s,%s,%s,%s,%s)""",
                      (b["bill_id"], b["account_id"], _d(gt["bill_period_start"]), _d(gt["bill_period_end"]), b["noise_level"]))
            c.execute("INSERT INTO bill_images (bill_id, image, mime) VALUES (%s,%s,'image/jpeg')",
                      (b["bill_id"], (s.seed_dir / b["image"]).read_bytes()))
            c.execute("INSERT INTO ground_truth (bill_id, fields) VALUES (%s,%s)", (b["bill_id"], json.dumps(gt)))
            added += 1
    if added:
        db.clear_cache()
    return added
