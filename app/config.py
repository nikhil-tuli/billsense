"""Runtime settings, read from environment variables on every call."""
import os
from dataclasses import dataclass
from pathlib import Path


def _float(name):
    v = os.environ.get(name)
    return float(v) if v else None


class ReadOnlyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_mode: str            # "deployed" allows writes; anything else is read-only
    gemini_api_key: str
    gemini_model: str
    mistral_api_key: str
    mistral_model: str
    gemini_min_interval_s: float   # 15 requests/min -> one call every 4s, plus margin
    gemini_daily_cap: int          # refuse new scans past this many extractor calls in the last 24h (limit is 500)
    mistral_rps: float             # 0.83 requests/sec
    mistral_tpm: int               # 20,000 tokens/min
    seed_dir: Path
    # Optional USD prices per 1M tokens, used for the "equivalent paid cost" metric.
    gemini_price_in: float | None
    gemini_price_out: float | None
    mistral_price_in: float | None
    mistral_price_out: float | None

    @property
    def writes_enabled(self) -> bool:
        return self.app_mode == "deployed"


def get_settings() -> Settings:
    root = Path(__file__).resolve().parent.parent
    return Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        app_mode=os.environ.get("APP_MODE", "local"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        mistral_api_key=os.environ.get("MISTRAL_API_KEY", ""),
        mistral_model=os.environ.get("MISTRAL_MODEL", "mistral-large-4"),
        gemini_min_interval_s=float(os.environ.get("GEMINI_MIN_INTERVAL_S", "4.1")),
        gemini_daily_cap=int(os.environ.get("GEMINI_DAILY_CAP", "450")),
        mistral_rps=float(os.environ.get("MISTRAL_RPS", "0.83")),
        mistral_tpm=int(os.environ.get("MISTRAL_TPM", "20000")),
        seed_dir=Path(os.environ.get("SEED_DIR", root / "seed_data")),
        gemini_price_in=_float("GEMINI_PRICE_IN_PER_M"),
        gemini_price_out=_float("GEMINI_PRICE_OUT_PER_M"),
        mistral_price_in=_float("MISTRAL_PRICE_IN_PER_M"),
        mistral_price_out=_float("MISTRAL_PRICE_OUT_PER_M"),
    )
