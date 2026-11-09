# BillSense — electricity bill extraction

NiceGUI app that scans stored (synthetic) electricity bill images one at a time with Gemini, stores the
extracted fields in Neon Postgres, and evaluates the extraction against ground truth and an LLM judge
(Mistral). See `requirements.md` for the agreed spec and `ARCHITECTURE.md` for the design and call flow.

## Layout
- `main.py` entry point; `app/ui.py` pages (Bills, Extracted data, Evaluation)
- `app/clients.py` Gemini extractor + Mistral judge (pacing, retries, usage capture)
- `app/services.py` scan / judge one bill; writes only when `APP_MODE=deployed`
- `app/compare.py`, `app/metrics.py` field comparison and evaluation metrics
- `app/db.py`, `app/schema.sql`, `app/seed.py` Neon access, schema, first-start seeding
- `seed_data/` 20 synthetic bills (jpg), ground truth JSON, `seed.json`; `tools/generate_bills.py` regenerates them
  (`python tools/generate_bills.py --out out --seed 42 --count 20`)

## Run
```
pip install -r requirements.txt
cp .env.example .env   # fill in values, then export them
python main.py         # http://localhost:8080
```
- **Local (default):** read-only. Browsing works; Scan/Judge show a notice and do nothing.
- **Deployed:** set `APP_MODE=deployed` (render.yaml does this). On first start the schema is created and
  `seed_data/` is loaded if the database is empty.

## Render
Create a Web Service from this repo (free plan). Set `DATABASE_URL` (Neon pooled string), `GEMINI_API_KEY`,
`MISTRAL_API_KEY`. `GEMINI_MODEL` / `MISTRAL_MODEL` default to `gemini-3.5-flash-lite` / `mistral-large-4`.

## Tests
```
pip install -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql://user@localhost:5432/billtest   # a throwaway Postgres; its public schema is DROPPED
pytest
```
Tests use fake Gemini/Mistral clients; no API keys needed. They do not call the live APIs.

## Notes
- Re-scan overwrites the bill's extraction and clears its judge verdicts; `extraction_runs` keeps every call.
- Optional `*_PRICE_IN_PER_M` / `*_PRICE_OUT_PER_M` env vars (USD per 1M tokens) enable the cost metric.
- `GEMINI_DAILY_CAP` (default 450) blocks new scans once that many extractor calls were made in the last 24h (free tier is 500/day).
- One provider call runs at a time; a second Scan/Judge click while one is in flight is refused with a notice.
- Latency = provider time of the final attempt (retries are counted separately). Dates are compared exactly as printed.
- Judge verdicts the model omits or garbles are stored as `missing` and counted as misses when the extractor was actually wrong.
- Extracted data tab: column chooser, filter rows by value, tick rows and download them as CSV (all 33 fields). Field guide tab: every field with its meaning.
- Evaluation tab: summary, per-bill table, field-mismatch table, per-field accuracy, judge quality, usage, latency (avg/median/P90/P95/max), reliability, and a split by noise level.
- No authentication (as agreed). Because anyone with the URL can trigger paid-quota calls, consider a separate read-only Neon role for local runs.
