import json

import httpx

from app.clients import GeminiExtractor, MistralJudge, TokenWindowLimiter
from app.config import get_settings


class RateLimitError(Exception):
    code = 429


class Resp:
    def __init__(self, text, tin=1000, tout=200, think=0):
        self.text = text
        self.usage_metadata = type("U", (), {"prompt_token_count": tin, "candidates_token_count": tout, "thoughts_token_count": think})()


class FakeGenai:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0
        self.models = self

    def generate_content(self, **kw):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def mk(outcomes):
    sleeps = []
    ex = GeminiExtractor(get_settings(), client=FakeGenai(outcomes), sleep=sleeps.append)
    return ex, sleeps


def test_gemini_ok_and_usage():
    ex, _ = mk([Resp(json.dumps({"account_id": "1"}), 1500, 200, 50)])
    r = ex.run(b"img")
    assert r.status == "ok" and r.data == {"account_id": "1"} and (r.input_tokens, r.output_tokens) == (1500, 250)


def test_gemini_retries_429_then_succeeds():
    ex, sleeps = mk([RateLimitError("429"), Resp("{}")])
    r = ex.run(b"img")
    assert r.status == "ok" and r.retries == 1 and 2 in sleeps


def test_gemini_rate_limit_exhausted_and_invalid_json_and_error():
    ex, _ = mk([RateLimitError("429")] * 4)
    r = ex.run(b"img")
    assert r.status == "rate_limited" and r.retries == 3
    ex, _ = mk([Resp("not json")])
    assert ex.run(b"img").status == "invalid_json"
    ex, _ = mk([ValueError("bad request")])
    r = ex.run(b"img")
    assert r.status == "error" and "bad request" in r.error and r.retries == 0


def test_gemini_interval_limiter_spaces_calls():
    clock = [0.0]
    sleeps = []
    ex = GeminiExtractor(get_settings(), client=FakeGenai([Resp("{}"), Resp("{}")]),
                         sleep=lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)), clock=lambda: clock[0])
    ex.run(b"a"); ex.run(b"b")
    assert sleeps and abs(sleeps[0] - get_settings().gemini_min_interval_s) < 0.01


def judge_with(handler):
    sleeps, t = [], [0.0]
    def sleep(x):
        sleeps.append(x)
        t[0] += x
    j = MistralJudge(get_settings(), http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleep, clock=lambda: t[0])
    return j, sleeps


def ok_body(content, p=3000, c=300):
    return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": p, "completion_tokens": c}}


def test_mistral_ok_sends_image_and_reads_usage():
    seen = {}
    def h(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json=ok_body(json.dumps({"verdicts": {"tax": "correct"}})))
    j, _ = judge_with(h)
    r = j.run(b"img", {"tax": 1.0})
    assert r.status == "ok" and r.data["verdicts"] == {"tax": "correct"} and (r.input_tokens, r.output_tokens) == (3000, 300)
    parts = seen["body"]["messages"][0]["content"]
    assert parts[1]["image_url"].startswith("data:image/jpeg;base64,") and seen["body"]["model"] == get_settings().mistral_model


def test_mistral_429_then_ok_and_failures():
    n = {"i": 0}
    def h(req):
        n["i"] += 1
        return httpx.Response(429, headers={"retry-after": "3"}) if n["i"] == 1 else httpx.Response(200, json=ok_body('{"verdicts": {}}'))
    j, sleeps = judge_with(h)
    r = j.run(b"i", {})
    assert r.status == "ok" and r.retries == 1 and 3.0 in sleeps
    j, _ = judge_with(lambda req: httpx.Response(200, json=ok_body("nope")))
    assert j.run(b"i", {}).status == "invalid_json"
    j, _ = judge_with(lambda req: httpx.Response(500, text="x"))
    assert j.run(b"i", {}).status == "error"
    j, _ = judge_with(lambda req: httpx.Response(429))
    assert j.run(b"i", {}).status == "rate_limited"


def test_token_window_limiter_waits_for_budget():
    t = [0.0]
    sleeps = []
    lim = TokenWindowLimiter(rps=0.83, tpm=20000, sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
    for _ in range(3):                     # 3 calls x 7000 tokens = 21000 > 20000 per minute
        lim.wait(); lim.record(7000)
    assert t[0] >= 60                  # the 3rd call had to wait for the 1st to leave the 60s window
    t2 = t[0]
    lim.wait()
    assert t[0] > t2 or sleeps         # a 4th call inside the window must wait, never skip the budget


class ServerError(Exception):
    code = 503


def test_gemini_retries_503_and_network_errors():
    ex, sleeps = mk([ServerError("unavailable"), ConnectionError("reset"), Resp("{}")])
    r = ex.run(b"img")
    assert r.status == "ok" and r.retries == 2 and 2 <= sleeps[0] < 3 and 4 <= sleeps[1] < 6


def test_mistral_retries_5xx_transport_and_tolerates_http_date_retry_after():
    n = {"i": 0}
    def h(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(503)
        if n["i"] == 2:
            return httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
        if n["i"] == 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=ok_body('{"verdicts": {}}'))
    j, sleeps = judge_with(h)
    r = j.run(b"i", {})
    assert r.status == "ok" and r.retries == 3 and len(sleeps) >= 3
    j, _ = judge_with(lambda req: httpx.Response(503))
    r = j.run(b"i", {})
    assert r.status == "error" and r.retries == 3                      # exhausted 5xx is an error, not rate_limited
