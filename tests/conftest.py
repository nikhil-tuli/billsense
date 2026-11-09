import json
import os
from pathlib import Path

import psycopg
import pytest

from app import db, services
from app.clients import CallResult
from app.compare import match
from app.config import get_settings
from app.fields import KEYS

pytest_plugins = ["nicegui.testing.user_plugin"]
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@localhost:54329/billtest")
SEED = Path(__file__).resolve().parent.parent / "seed_data"


def truth_of(bill_id):
    return json.loads((SEED / "ground_truth" / f"{bill_id}.json").read_text())


class FakeExtractor:
    """Returns the ground truth for whichever bill image it is given (matched by call order via a queue)."""
    def __init__(self, perturb=None, status="ok"):
        self.perturb, self.status, self.bill_id, self.calls = perturb or {}, status, None, 0

    def run(self, image, mime="image/jpeg"):
        self.calls += 1
        if self.status != "ok":
            return CallResult(self.status, error="boom", latency_ms=50, retries=1)
        data = dict(truth_of(self.bill_id))
        data.update(self.perturb)
        return CallResult("ok", data, 1200, 300, 800 + self.calls, 0)


class FakeJudge:
    """A judge that is right except for fields listed in `blind` (always says correct)."""
    def __init__(self, bill_id=None, blind=()):
        self.bill_id, self.blind, self.calls = bill_id, set(blind), 0

    def run(self, image, extracted, mime="image/jpeg"):
        self.calls += 1
        t = truth_of(self.bill_id)
        v = {k: ("correct" if (match(k, t.get(k), extracted.get(k)) or k in self.blind) else "incorrect") for k in KEYS}
        return CallResult("ok", {"verdicts": v}, 5000, 400, 2500, 0)


def _assert_safe_test_db(url):
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.hostname not in ("localhost", "127.0.0.1") or "test" not in (u.path or "").lower():
        raise RuntimeError(f"Refusing to DROP SCHEMA on {u.hostname}{u.path}: tests only run against a local database whose name contains 'test'.")


@pytest.fixture
def fresh_db(monkeypatch):
    _assert_safe_test_db(TEST_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("APP_MODE", "deployed")
    try:
        with psycopg.connect(TEST_URL, autocommit=True) as c:
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    except Exception as e:                                     # noqa: BLE001
        pytest.skip(f"no test database: {e}")
    db.close_pool()
    db.init_schema()
    from app import seed
    assert seed.seed_missing() == 100
    yield
    services.set_clients(None, None)
    db.close_pool()
