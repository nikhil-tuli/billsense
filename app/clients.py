"""Gemini extractor and Mistral judge clients, with pacing, retries and usage capture."""
import base64
import json
import threading
import time
from collections import deque
from dataclasses import dataclass

from app.config import Settings
from app.fields import BillExtraction, KEYS

EXTRACT_PROMPT = (
    "You are reading a photo or scan of an electricity bill. Extract every field below exactly as printed "
    "on the bill. Dates: DD/MM/YYYY. Numbers: plain numbers, no currency symbols or thousands separators. "
    "Bill period start and end are the two dates printed after 'Bill Period'. "
    "If a field is missing or unreadable, use null. Do not infer or compute any value.\n"
    "Fields: " + ", ".join(KEYS)
)

JUDGE_PROMPT = (
    "You verify data extracted from the electricity bill image. For EVERY field in the extracted JSON below, "
    "decide whether its value matches what is printed on the bill. Ignore formatting differences "
    "(spaces, commas, currency symbols, trailing zeros). A null value for a field that is printed on the bill "
    "is incorrect. Reply with JSON only, in this exact shape: "
    '{"verdicts": {"<field_name>": "correct" or "incorrect", ...}} covering every field.\n'
    "Extracted JSON:\n"
)


@dataclass
class CallResult:
    status: str                       # ok | invalid_json | rate_limited | error
    data: dict | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    retries: int = 0
    error: str | None = None


RETRY_CODES = {500, 502, 503, 504}


def _code(exc):
    return getattr(exc, "code", None) or getattr(exc, "status_code", None)


def _is_rate_limit(exc) -> bool:
    return _code(exc) == 429 or "RESOURCE_EXHAUSTED" in str(exc) or " 429" in str(exc)


def _is_retriable(exc) -> bool:
    """429, transient 5xx and network-level failures are worth retrying."""
    return (_is_rate_limit(exc) or _code(exc) in RETRY_CODES
            or isinstance(exc, (ConnectionError, TimeoutError)) or type(exc).__name__ in ("ConnectError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError", "ReadError"))


def _retry_after(headers, default):
    try:
        return max(0.0, min(float(headers.get("retry-after", default)), 60.0))
    except (TypeError, ValueError):                 # e.g. an HTTP-date
        return default


def _parse_json(text):
    if text is None:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


class IntervalLimiter:
    """At most one call every `interval` seconds (thread-safe)."""
    def __init__(self, interval, sleep=time.sleep, clock=time.monotonic):
        self.interval, self.sleep, self.clock, self.last, self.lock = interval, sleep, clock, None, threading.Lock()

    def wait(self):
        with self.lock:
            if self.last is not None:
                gap = self.interval - (self.clock() - self.last)
                if gap > 0:
                    self.sleep(gap)
            self.last = self.clock()


class TokenWindowLimiter:
    """Keeps requests/sec and tokens/minute under the provider's limits (sliding 60s window)."""
    def __init__(self, rps, tpm, sleep=time.sleep, clock=time.monotonic):
        self.min_gap, self.tpm, self.sleep, self.clock = 1.0 / rps, tpm, sleep, clock
        self.events = deque()      # (time, tokens)
        self.last = None
        self.est = 6000            # first-call estimate; replaced by observed usage
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            while True:
                now = self.clock()
                while self.events and now - self.events[0][0] >= 60:
                    self.events.popleft()
                used = sum(t for _, t in self.events)
                delay = 0.0
                if self.last is not None:
                    delay = max(delay, self.min_gap - (now - self.last))
                if self.events and used + self.est > self.tpm:
                    delay = max(delay, 60 - (now - self.events[0][0]) + 0.05)
                if delay <= 0:
                    self.last = now
                    self.events.append((now, self.est))
                    return
                self.sleep(delay)

    def record(self, tokens):
        with self.lock:
            if self.events:
                self.events[-1] = (self.events[-1][0], tokens)
            self.est = max(tokens, 1000)


class GeminiExtractor:
    def __init__(self, settings: Settings, client=None, sleep=time.sleep, clock=time.monotonic):
        self.s, self._client, self.sleep, self.clock = settings, client, sleep, clock
        self.limiter = IntervalLimiter(settings.gemini_min_interval_s, sleep, clock)

    @property
    def client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.s.gemini_api_key)
        return self._client

    def run(self, image: bytes, mime: str = "image/jpeg") -> CallResult:
        from google.genai import types
        retries, last_err = 0, None
        for attempt in range(4):
            self.limiter.wait()
            t0 = self.clock()
            try:
                resp = self.client.models.generate_content(
                    model=self.s.gemini_model,
                    contents=[types.Part.from_bytes(data=image, mime_type=mime), EXTRACT_PROMPT],
                    config=types.GenerateContentConfig(response_mime_type="application/json",
                                                       response_schema=BillExtraction, temperature=0.0))
            except Exception as e:                      # noqa: BLE001 - provider errors vary
                last_err = e
                if _is_retriable(e) and attempt < 3:
                    retries += 1
                    self.sleep(2 ** (attempt + 1))
                    continue
                return CallResult("rate_limited" if _is_rate_limit(e) else "error", retries=retries,
                                  error=str(e)[:500], latency_ms=int((self.clock() - t0) * 1000))
            ms = int((self.clock() - t0) * 1000)
            u = getattr(resp, "usage_metadata", None)
            tin = getattr(u, "prompt_token_count", None)
            tout = (getattr(u, "candidates_token_count", None) or 0) + (getattr(u, "thoughts_token_count", None) or 0)
            data = _parse_json(getattr(resp, "text", None))
            if data is None:
                return CallResult("invalid_json", input_tokens=tin, output_tokens=tout, latency_ms=ms,
                                  retries=retries, error="response was not a JSON object")
            return CallResult("ok", data, tin, tout, ms, retries)
        return CallResult("error", retries=retries, error=str(last_err)[:500])


class MistralJudge:
    URL = "https://api.mistral.ai/v1/chat/completions"

    def __init__(self, settings: Settings, http=None, sleep=time.sleep, clock=time.monotonic):
        self.s, self._http, self.sleep, self.clock = settings, http, sleep, clock
        self.limiter = TokenWindowLimiter(settings.mistral_rps, settings.mistral_tpm, sleep, clock)

    @property
    def http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=90)
        return self._http

    def run(self, image: bytes, extracted: dict, mime: str = "image/jpeg") -> CallResult:
        body = {
            "model": self.s.mistral_model, "temperature": 0.0, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": JUDGE_PROMPT + json.dumps(extracted, ensure_ascii=False)},
                {"type": "image_url", "image_url": f"data:{mime};base64,{base64.b64encode(image).decode()}"}]}],
        }
        headers = {"Authorization": f"Bearer {self.s.mistral_api_key}"}
        retries, last_err = 0, None
        for attempt in range(4):
            self.limiter.wait()
            t0 = self.clock()
            try:
                r = self.http.post(self.URL, json=body, headers=headers)
            except Exception as e:                      # noqa: BLE001
                last_err = e
                if _is_retriable(e) and attempt < 3:
                    retries += 1
                    self.sleep(2 ** (attempt + 1))
                    continue
                return CallResult("error", retries=retries, error=f"{type(e).__name__}: {e}"[:500])
            ms = int((self.clock() - t0) * 1000)
            if (r.status_code == 429 or r.status_code in RETRY_CODES) and attempt < 3:
                retries += 1
                last_err = f"HTTP {r.status_code}"
                self.sleep(_retry_after(r.headers, 2 ** (attempt + 1)))
                continue
            if r.status_code != 200:
                return CallResult("rate_limited" if r.status_code == 429 else "error", retries=retries,
                                  latency_ms=ms, error=f"HTTP {r.status_code}: {r.text[:300]}")
            j = r.json()
            u = j.get("usage") or {}
            tin, tout = u.get("prompt_tokens"), u.get("completion_tokens")
            self.limiter.record((tin or 0) + (tout or 0))
            try:
                data = _parse_json(j["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError):
                data = None
            if data is None or not isinstance(data.get("verdicts"), dict):
                return CallResult("invalid_json", input_tokens=tin, output_tokens=tout, latency_ms=ms,
                                  retries=retries, error="response had no verdicts object")
            return CallResult("ok", data, tin, tout, ms, retries)
        return CallResult("rate_limited" if str(last_err) == "HTTP 429" else "error", retries=retries, error=str(last_err))
