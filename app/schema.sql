CREATE TABLE IF NOT EXISTS owners (
    owner_id   SERIAL PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS properties (
    property_id   INTEGER PRIMARY KEY,
    owner_id      INTEGER NOT NULL REFERENCES owners(owner_id) ON DELETE CASCADE,
    account_id    TEXT UNIQUE NOT NULL,          -- 1:1 with the property
    rr_number     TEXT,
    property_name TEXT NOT NULL,
    city          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bills (
    bill_id           TEXT PRIMARY KEY,
    account_id        TEXT NOT NULL REFERENCES properties(account_id) ON DELETE CASCADE,
    bill_period_start DATE NOT NULL,
    bill_period_end   DATE NOT NULL,
    scan_status       TEXT NOT NULL DEFAULT 'not_scanned',   -- not_scanned | scanned
    noise_level       TEXT,                                  -- clean | medium | noisy (for the breakdown)
    UNIQUE (account_id, bill_period_start)
);

CREATE TABLE IF NOT EXISTS bill_images (
    bill_id TEXT PRIMARY KEY REFERENCES bills(bill_id) ON DELETE CASCADE,
    image   BYTEA NOT NULL,
    mime    TEXT NOT NULL DEFAULT 'image/jpeg'
);

-- One row per bill, overwritten on re-scan.
CREATE TABLE IF NOT EXISTS extractions (
    bill_id           TEXT PRIMARY KEY REFERENCES bills(bill_id) ON DELETE CASCADE,
    account_id        TEXT,
    rr_number         TEXT,
    bill_number       TEXT,
    bill_period_start TEXT,
    bill_period_end   TEXT,
    units_consumed    NUMERIC,
    fixed_charges     NUMERIC,
    energy_charges    NUMERIC,
    tax               NUMERIC,
    net_payable       NUMERIC,
    due_date          TEXT,
    secondary_fields  JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_json          JSONB,                        -- the extractor's raw response, shown to the judge
    model             TEXT,
    extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ground_truth (
    bill_id TEXT PRIMARY KEY REFERENCES bills(bill_id) ON DELETE CASCADE,
    fields  JSONB NOT NULL
);

-- Append-only call log (survives re-scans), one row per extractor/judge call.
CREATE TABLE IF NOT EXISTS extraction_runs (
    run_id        BIGSERIAL PRIMARY KEY,
    bill_id       TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    role          TEXT NOT NULL,                 -- extractor | judge
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    latency_ms    INTEGER,
    status        TEXT NOT NULL,                 -- ok | invalid_json | rate_limited | error
    error         TEXT,
    retries       INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_results (
    bill_id                  TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    field                    TEXT NOT NULL,
    truth                    TEXT,
    extracted                TEXT,
    extractor_match          BOOLEAN NOT NULL,
    judge_verdict            TEXT,               -- correct | incorrect | missing (judge omitted it) | NULL (not judged)
    judge_agrees_with_truth  BOOLEAN,
    PRIMARY KEY (bill_id, field)
);

ALTER TABLE extractions ADD COLUMN IF NOT EXISTS raw_json JSONB;
