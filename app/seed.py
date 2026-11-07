"""Load the bundled synthetic data into Neon (only when writes are enabled and the DB is empty)."""
import json
from datetime import datetime

from app import db
from app.config import get_settings


def _d(s):
    return datetime.strptime(s, "%d/%m/%Y").date()


def seed_if_empty() -> bool:
    s = get_settings()
    if not s.writes_enabled:
        return False
    if db.one("SELECT 1 AS x FROM properties LIMIT 1"):
        return False
    seed = json.loads((s.seed_dir / "seed.json").read_text())
    with db.tx() as c:
        owner_id = {}
        for name in seed["owners"]:
            owner_id[name] = c.execute("INSERT INTO owners (name) VALUES (%s) RETURNING owner_id", (name,)).fetchone()["owner_id"]
        for p in seed["properties"]:
            c.execute("""INSERT INTO properties (property_id, owner_id, account_id, rr_number, property_name, city)
                         VALUES (%s,%s,%s,%s,%s,%s)""",
                      (p["property_id"], owner_id[p["owner"]], p["account_id"], p["rr_number"], p["property_name"], p["city"]))
        for b in seed["bills"]:
            gt = json.loads((s.seed_dir / b["ground_truth"]).read_text())
            c.execute("""INSERT INTO bills (bill_id, account_id, bill_period_start, bill_period_end, noise_level)
                         VALUES (%s,%s,%s,%s,%s)""",
                      (b["bill_id"], b["account_id"], _d(gt["bill_period_start"]), _d(gt["bill_period_end"]), b["noise_level"]))
            c.execute("INSERT INTO bill_images (bill_id, image, mime) VALUES (%s,%s,'image/jpeg')",
                      (b["bill_id"], (s.seed_dir / b["image"]).read_bytes()))
            c.execute("INSERT INTO ground_truth (bill_id, fields) VALUES (%s,%s)", (b["bill_id"], json.dumps(gt)))
    return True
