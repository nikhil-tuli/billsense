"""Neon Postgres access (psycopg 3, small pool). Keep image bytes out of list queries."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.config import ReadOnlyError, get_settings
from app.fields import PRIMARY

_pool = None


def _require_writes():
    """Defence in depth: every write path refuses to run unless APP_MODE=deployed."""
    if not get_settings().writes_enabled:
        raise ReadOnlyError("Read-only mode: writes only work in the deployed app.")


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        url = get_settings().database_url
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        _pool = ConnectionPool(
            conninfo=url, min_size=0, max_size=4, timeout=30, open=True,
            kwargs={"row_factory": dict_row, "autocommit": True, "prepare_threshold": None, "connect_timeout": 15},
            check=ConnectionPool.check_connection,   # recovers connections dropped by Neon auto-suspend
        )
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def q(sql, params=None):
    with pool().connection() as c:
        return c.execute(sql, params).fetchall()


def one(sql, params=None):
    rows = q(sql, params)
    return rows[0] if rows else None


@contextmanager
def tx():
    with pool().connection() as c:
        with c.transaction():
            yield c


def init_schema():
    _require_writes()
    sql = (Path(__file__).parent / "schema.sql").read_text()
    with pool().connection() as c:
        c.execute(sql)


# ---- reads (no image bytes) -------------------------------------------------
def properties():
    return q("""SELECT p.property_id, p.property_name, p.city, p.account_id, p.rr_number, o.name AS owner
                FROM properties p JOIN owners o USING (owner_id) ORDER BY p.city, o.name, p.property_name""")


def bills():
    return q("""SELECT b.bill_id, b.account_id, b.bill_period_start, b.bill_period_end, b.scan_status,
                       b.noise_level, p.property_name, p.city, o.name AS owner
                FROM bills b JOIN properties p USING (account_id) JOIN owners o USING (owner_id)
                ORDER BY b.bill_id""")


def get_bill(bill_id):
    return one("""SELECT b.bill_id, b.account_id, b.bill_period_start, b.bill_period_end, b.scan_status,
                         b.noise_level, p.property_name, p.city, o.name AS owner
                  FROM bills b JOIN properties p USING (account_id) JOIN owners o USING (owner_id)
                  WHERE b.bill_id=%s""", (bill_id,))


def get_extraction(bill_id):
    return one("SELECT * FROM extractions WHERE bill_id=%s", (bill_id,))


def extraction_fields(row) -> dict:
    """Flatten an extractions row (primary columns + secondary JSONB) into one field dict."""
    if not row:
        return {}
    f = {k: row[k] for k in PRIMARY}
    f.update(row["secondary_fields"] or {})
    return {k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in f.items()}


def extracted_rows():
    return q("""SELECT b.bill_id, b.noise_level, p.property_name, p.city, o.name AS owner, e.*
                FROM extractions e JOIN bills b ON b.bill_id = e.bill_id JOIN properties p ON p.account_id = b.account_id
                JOIN owners o ON o.owner_id = p.owner_id ORDER BY b.bill_id""")


def get_image(bill_id):
    return one("SELECT image, mime FROM bill_images WHERE bill_id=%s", (bill_id,))


def get_truth(bill_id):
    r = one("SELECT fields FROM ground_truth WHERE bill_id=%s", (bill_id,))
    return r["fields"] if r else None


def all_runs():
    return q("""SELECT r.*, b.noise_level FROM extraction_runs r JOIN bills b USING (bill_id) ORDER BY run_id""")


def all_eval():
    return q("""SELECT e.*, b.noise_level FROM eval_results e JOIN bills b USING (bill_id)
                ORDER BY e.bill_id, e.field""")


def eval_for(bill_id):
    return q("SELECT * FROM eval_results WHERE bill_id=%s", (bill_id,))


def judged_bills():
    return {r["bill_id"] for r in q("SELECT DISTINCT bill_id FROM eval_results WHERE judge_verdict IS NOT NULL")}


# ---- writes ------------------------------------------------------------------
def log_run(bill_id, role, model, res):
    _require_writes()
    q("""INSERT INTO extraction_runs (bill_id, role, model, input_tokens, output_tokens, latency_ms, status, error, retries)
         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING run_id""",
      (bill_id, role, model, res.input_tokens, res.output_tokens, res.latency_ms, res.status, res.error, res.retries))


def save_extraction(bill_id, fields: dict, model: str, eval_rows: list[dict]):
    """Overwrite the bill's extraction and its extractor comparison rows (judge verdicts reset)."""
    _require_writes()
    prim = {k: fields.get(k) for k in PRIMARY}
    sec = {k: v for k, v in fields.items() if k not in PRIMARY}
    with tx() as c:
        c.execute("""INSERT INTO extractions (bill_id, account_id, rr_number, bill_number, bill_period_start,
                       bill_period_end, units_consumed, fixed_charges, energy_charges, tax, net_payable, due_date,
                       secondary_fields, raw_json, model, extracted_at)
                     VALUES (%(bill_id)s,%(account_id)s,%(rr_number)s,%(bill_number)s,%(bill_period_start)s,
                       %(bill_period_end)s,%(units_consumed)s,%(fixed_charges)s,%(energy_charges)s,%(tax)s,
                       %(net_payable)s,%(due_date)s,%(sec)s,%(raw)s,%(model)s,%(now)s)
                     ON CONFLICT (bill_id) DO UPDATE SET account_id=EXCLUDED.account_id, rr_number=EXCLUDED.rr_number,
                       bill_number=EXCLUDED.bill_number, bill_period_start=EXCLUDED.bill_period_start,
                       bill_period_end=EXCLUDED.bill_period_end, units_consumed=EXCLUDED.units_consumed,
                       fixed_charges=EXCLUDED.fixed_charges, energy_charges=EXCLUDED.energy_charges,
                       tax=EXCLUDED.tax, net_payable=EXCLUDED.net_payable, due_date=EXCLUDED.due_date,
                       secondary_fields=EXCLUDED.secondary_fields, raw_json=EXCLUDED.raw_json, model=EXCLUDED.model,
                       extracted_at=EXCLUDED.extracted_at""",
                  {"bill_id": bill_id, **{k: _db_val(k, v) for k, v in prim.items()},
                   "sec": Jsonb(sec), "raw": Jsonb(fields), "model": model, "now": datetime.now().astimezone()})
        c.execute("DELETE FROM eval_results WHERE bill_id=%s", (bill_id,))
        for r in eval_rows:
            c.execute("""INSERT INTO eval_results (bill_id, field, truth, extracted, extractor_match)
                         VALUES (%s,%s,%s,%s,%s)""", (bill_id, r["field"], r["truth"], r["extracted"], r["extractor_match"]))
        c.execute("UPDATE bills SET scan_status='scanned' WHERE bill_id=%s", (bill_id,))


def _db_val(k, v):
    from app.fields import TYPES
    from app.compare import _num
    if v is None:
        return None
    if TYPES[k] in (int, float):
        return _num(v)
    return str(v)


def save_judge(bill_id, verdicts: dict):
    """verdicts: field -> 'correct' | 'incorrect'. Fields the judge omitted or garbled are stored as 'missing'."""
    _require_writes()
    with tx() as c:
        for r in c.execute("SELECT field, extractor_match FROM eval_results WHERE bill_id=%s", (bill_id,)).fetchall():
            v = verdicts.get(r["field"])
            v = v if v in ("correct", "incorrect") else "missing"
            if v == "missing":
                agrees = False if not r["extractor_match"] else None   # an omitted true error is a miss
            else:
                agrees = (v == "correct") == r["extractor_match"]
            c.execute("UPDATE eval_results SET judge_verdict=%s, judge_agrees_with_truth=%s WHERE bill_id=%s AND field=%s",
                      (v, agrees, bill_id, r["field"]))


def runs_last_24h(role):
    return one("SELECT count(*) AS n FROM extraction_runs WHERE role=%s AND created_at > now() - interval '24 hours'",
               (role,))["n"]
