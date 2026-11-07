# Electricity Bill OCR Extraction Tool: Requirements (v0.1 draft)

Separate repository from `doc-ocr-pipeline`. Scope: electricity bills only. A chatbot will be added later (out of scope for v0.1).

## 1. Goals
- Let a user browse already-uploaded bills, scan them one at a time with an LLM, and see the extracted data next to the bill image.
- Measure how accurate the extraction is, using synthetic bills with known ground truth.

## 2. Out of scope (v0.1)
- Bulk or background processing, authentication, blob storage, semantic search, chatbot, PaddleOCR, Kannada or other non-English bills, real customer bills.

## 3. Data and assumptions
- All data is synthetic. Bills are English-only, in a BESCOM-like layout with a fictional utility name and a "SAMPLE" mark.
- 20 bills for v0.1 (20 properties, 1 bill each), from 12 owners across Indian metros (Bengaluru, Mumbai, Delhi, Chennai, Hyderabad, Pune, Kolkata). Some owners hold several properties in different cities.
- A seed file maps each bill to City, Owner and Property Name. Ground-truth JSON is generated alongside each bill image.
- The synthetic bills are generated outside this repo (`generate_bills.py`). This repo only loads them.

## 4. Functional requirements

### 4.1 Tabs
1. **Bills**: browse and scan bills.
2. **Extracted Data**: all bills that already have extracted data.
3. **Evaluation**: accuracy, judge and usage metrics for the 20-bill sample.

### 4.2 Bills tab
- Three cascading filters: **City**, **Owner**, **Property Name**. Choosing any one narrows the other two to matching values (e.g. choosing a city shows only that city's owners and properties). Any one of the three can be the starting filter.
- Choosing a Property Name shows that property's bill list.
- Each bill has a **Scan** button. Scanning runs extraction on the existing stored image. No new image is generated. Scanning is one bill at a time, from the UI.
- **Account ID** is the leftmost data column. It is blank until the bill is scanned, then shows the account ID read from the image.
- **Bill period** always shows the billing period from master data, because it identifies the bill when a property has several periods.
- Each row expands in place (like the Extracted Data tab). The stored bill image is on the left (click to enlarge); on the right is a field table with columns Field, Extracted, Ground truth, Match (tick/cross) and Judge verdict (once judged). By default the 11 primary fields are shown, with toggles for all 33 fields and for mismatches only. An unscanned bill shows the image and a "not scanned" note.
- Re-scanning **overwrites** the earlier extraction. No history of extracted data is kept.

### 4.3 Extracted Data tab
- Table with one row per scanned bill.
- Columns: City, Owner, Property Name, then the primary fields below.
- Clicking a row expands to show the secondary fields and the bill image.

### 4.4 Fields
- **Primary (table columns):** Account ID, RR No, Bill No, Bill Period, Units Consumed, Fixed Charges, Energy Charges, Tax, Net Payable, Due Date.
- **Secondary (shown on click):** Tariff, Sanctioned Load, Reading Date, Previous and Present Reading, Recorded MD, Power Factor, FPPCA, Surcharge, Interest, Debit/Credit, Arrears, ASD.

### 4.5 Keys
- Property ↔ Account ID is **1:1**. `account_id` is the unique key for a property. RR No is a stored attribute.
- A bill is unique on (`account_id`, `bill_period_start`).

### 4.6 Evaluation tab
- Run on **all 20 bills**, generated with a fixed seed (42) and split across clean (7), medium (7) and noisy (6) bills.
- Shows Gemini extraction vs ground truth, and LLM-judge verdicts vs ground truth, per field and per bill.
- Workflow: scan each bill on the Bills tab, then press Judge on the Evaluation tab for that bill. Both steps are one bill at a time; there is no bulk job.
- Shows the metrics in section 7.
- Per-field exact match is explicitly extractor vs ground truth. It lists the 10 lowest-matching fields by default, with an option to show all 33.
- The judge result is shown as a confusion matrix (rows: extractor wrong / right; columns: judge incorrect / correct / no verdict), with precision and recall below it.
- Short definitions are shown for arithmetic consistency, noise level and "fields without verdict".
- The existing field-mismatch and judge-disagreement tables are kept as they are.

## 5. Technical requirements
- **Database:** Neon Postgres is the only data store. Images are stored in Neon as `bytea` in their own table, and are never loaded for list views. No blob storage.
- **Extraction model:** `gemini-3.5-flash-lite` (Gemini free tier). Verified limits: 15 requests/min, 500 requests/day, 250,000 input tokens/min. The model id is pinned, never an alias, and kept as a config value. Output is JSON against a fixed schema. Retry with backoff on 429.
- **Judge model:** `mistral-large-4` (Mistral free plan). Verified limits: 20,000 tokens/min, 0.83 requests/sec. Pin the exact id, never a `-latest` alias. The judge sees the bill image and the extracted JSON, and returns a verdict per field.
- **Frontend:** NiceGUI hosted on Render (free tier).
- **Seeding:** on first start in deployed mode, if the database is empty, the app loads the bundled `seed_data/` (20 images, ground truth, owners, properties). Local mode never writes.
- **Read-only local:** extraction and DB writes work only in the deployed app. Local runs are read-only, and the Gemini and Mistral keys exist only as Render environment variables.
- **Pacing:** UI scans are one at a time, well inside the limits. The 20-bill evaluation run is paced to stay under 15 extractor requests/min and the judge's 20,000 tokens/min.
- **Latency:** list and filter views must stay responsive. Cold starts on Render and Neon free tiers are accepted but must be measured.
- **Process:** a coding agent writes the code, then a separate review agent does a senior-engineer review before every deployment. Review checklist: secrets handling, SQL safety, idempotent writes, error and quota handling, read-only enforcement locally, tests passing.

## 6. Data model (Neon)
| Table | Purpose |
|---|---|
| `owners` | owner_id, name |
| `properties` | property_id, owner_id, account_id (unique), rr_number, property_name, city |
| `bills` | bill_id, account_id, bill_period_start/end, scan_status, noise_level; unique (account_id, bill_period_start) |
| `bill_images` | bill_id, image (`bytea`), mime |
| `extractions` | bill_id (one row, overwritten), primary-field columns, secondary_fields (JSONB), model, extracted_at |
| `ground_truth` | bill_id, fields (JSONB) |
| `extraction_runs` | append-only call log: bill_id, role (extractor/judge), model, input_tokens, output_tokens, latency_ms, status, error, retries, created_at |
| `eval_results` | bill_id, field, truth, extracted, extractor_match, judge_verdict, judge_agrees_with_truth |

Note: extracted data is overwritten on re-scan, but `extraction_runs` stays append-only so usage and latency metrics are not lost.

## 7. Metrics
- **Usage:** input and output tokens per call and in total, with the equivalent paid cost.
- **Latency:** average, median, P90, P95, max, for extractor and judge.
- **Reliability:** error, 429 and retry rate; valid-JSON-against-schema rate.
- **Extraction accuracy (vs ground truth):** per-field exact match, critical fields reported separately; bill-level accuracy (all critical fields correct); arithmetic-consistency pass rate.
- **Judge quality:** judge vs ground-truth agreement (precision/recall on "field is wrong"), and a list of disagreements.
- **Breakdown:** all of the above split by noise level.

## 8. Risks and open items
- **Deferred: ground truth is not independent of the images.** Today the generator creates each bill's values first, writes them to the ground-truth JSON, and renders the image from the same values. That is acceptable for this synthetic 20-bill evaluation but does not scale or reflect real bills. Later options: human-verified labels on real bills, or a separately keyed ground-truth source. Revisit before any real-bill or larger-scale evaluation.
- **Judge strength:** a judge weaker than the extractor may miss real errors. Large 4 is Mistral's largest current multimodal model, and it is a different model family from the extractor. Judge-vs-ground-truth agreement is therefore a required result, and we should be ready to swap the judge model.
- **Free-tier terms:** limits are verified (section 5). A third-party tracker reports that Mistral free-plan data may be used for model improvement unless opted out; confirm in your Mistral account settings. Gemini free-tier content may be used to improve Google's products, so no real bills.
- **Render/Neon cold starts:** verify current free-tier sleep behaviour against the latency requirement.
- **"Scan" wording:** interpreted as running extraction on an existing stored image, with no new image generated.

## Implementation addenda (post-review)
- `extractions.raw_json` stores the model's raw output; the judge sees raw values, not the normalised ones.
- Daily guard `GEMINI_DAILY_CAP`; single in-flight provider call; retries with backoff on 429/5xx/network, honouring Retry-After.
- Arithmetic consistency is reported as pass/fail/not-checkable (null inputs are not failures).
- Evaluation adds per-bill and field-mismatch tables. Authentication remains out of scope.
