# Electricity Bill OCR Extraction Tool: Requirements (v0.2 draft)

Separate repository from `doc-ocr-pipeline`. Scope: electricity bills only. A chatbot will be added later (out of scope for v0.1).

## 1. Goals
- Let a user browse already-uploaded bills, scan them one at a time with an LLM, and see the extracted data next to the bill image.
- Measure how accurate the extraction is, using synthetic bills with known ground truth.

## 2. Out of scope (v0.1)
- Bulk or background processing, authentication, blob storage, semantic search, chatbot, PaddleOCR, Kannada or other non-English bills, real customer bills.

## 3. Data and assumptions
- All data is synthetic. Bills are English-only, in a BESCOM-like layout with a fictional utility name and a "SAMPLE" mark.
- **100 sample bills** (100 properties, 1 bill each). v0.1 shipped the first 20 (12 owners, 7 cities: Bengaluru, Mumbai, Delhi, Chennai, Hyderabad, Pune, Kolkata); v0.2 appends 80 more. See 3a.
- Some owners hold several properties in different cities.
- A seed file maps each bill to City, Owner and Property Name. Ground-truth JSON is generated alongside each bill image.
- The synthetic bills are generated outside this repo (`generate_bills.py`). This repo only loads them.

### 3a. Scale-up to 100 bills (v0.2)
- **Scope:** 100 properties, 1 bill each. No change to fields, tabs or models.
- **Data mix (dummy, decided by us):**
  - Owners: about 40 in total (the existing 12 plus about 28 new). Some own several properties across cities.
  - Cities: 10 (the existing 7 plus Ahmedabad, Jaipur, Lucknow). Roughly 10 properties per city.
  - Noise: same proportions. Existing 20 stay 7/7/6. The new 80 are 28 clean / 28 medium / 24 noisy, giving 35/35/30 overall.
- **Generation:** `generate_bills.py` is extended to emit bills 21 to 100 with seed 42. Bills 1 to 20 stay byte-identical, so existing ground truth and scan results remain valid.
- **Append only:** previous data is never cleaned. The existing 20 bills, their scan results, judge verdicts, `extraction_runs` and all uploads stay untouched.
  - The seeder runs at startup in deployed mode and inserts only bills, properties and owners that are missing. It never updates or deletes an existing row.
  - It is idempotent: a second start adds nothing.
  - New owners, properties and cities are matched by name, so existing ones are reused, not duplicated.
  - Uploaded bills (`upl_NNN`, `UPL-n`) cannot clash with sample ids (`bill_NNN`).
  - New bills start as "Not scanned".
- **Cost and limits:** a full run is 100 Gemini and 100 Mistral calls, one bill at a time. This stays under the Gemini 500/day cap. At 15 requests/min a full scan takes about 7 minutes. Accepted.
- **Storage:** about 20 MB more in Neon. Accepted.
- **UI:** Bills, Extracted data and Evaluation tabs must stay responsive with 100 bills (first paint under 1 s from cache). Filters, counts and metrics cover all 100.

## 4. Functional requirements

### 4.1 Tabs
1. **Bills**: browse and scan bills.
2. **Upload** (section 4.8): upload your own bill file, with history.
3. **Extracted Data**: all bills that already have extracted data.
4. **Evaluation**: accuracy, judge and usage metrics for the 100-bill sample.
5. **Field guide**: all 33 bill fields with their meaning and whether they are critical (static; source `app/fields.py`).

### 4.2 Bills tab
- Five cascading filters: **City**, **Owner**, **Property Name**, **Bill reading period**, **Scan status** (All / Scanned / Not scanned). Choosing any one narrows the others to matching values. Any one can be the starting filter. Reading-period options come from scanned bills only, so unscanned bills show only under "All".
- Choosing a Property Name shows that property's bill list.
- Each bill has a **Scan** button. Scanning runs extraction on the existing stored image. No new image is generated. Scanning is one bill at a time, from the UI.
- **Account ID** is the leftmost data column. It is blank until the bill is scanned, then shows the account ID read from the image.
- **Bill reading period** shows the month and year of the extracted bill period end (e.g. "Nov 2026"). It is blank until the bill is scanned.
- The bill image is shown in the expanded row regardless of scan status.
- Each row expands in place (like the Extracted Data tab). The stored bill image is on the left (click to enlarge); on the right is a field table with columns Field, Extracted, Ground truth, Match (tick/cross) and Judge verdict (once judged). By default the 11 primary fields are shown, with toggles for all 33 fields and for mismatches only. An unscanned bill shows the image and a "not scanned" note.
- Re-scanning **overwrites** the earlier extraction. No history of extracted data is kept.

### 4.3 Extracted Data tab
- Table with one row per scanned bill. No row expansion.
- Columns: City, Owner, Property Name, then all 33 fields. The 11 primary fields are visible by default; a **column chooser** shows or hides the others.
- **Row filter by value:** pick a field, type text, Add filter; a row is kept if the field contains the text (case-insensitive). Filters combine (AND) and show as removable chips.
- **CSV download:** tick one or more rows (header box selects all shown rows) and press "Download CSV (n selected)". The CSV has City, Owner, Property name and all 33 fields, regardless of visible columns.

### 4.4 Fields
- **Primary (table columns):** Account ID, RR No, Bill No, Bill Period, Units Consumed, Fixed Charges, Energy Charges, Tax, Net Payable, Due Date.
- **Secondary (shown on click):** Tariff, Sanctioned Load, Reading Date, Previous and Present Reading, Recorded MD, Power Factor, FPPCA, Surcharge, Interest, Debit/Credit, Arrears, ASD.

### 4.5 Keys
- Property ↔ Account ID is **1:1**. `account_id` is the unique key for a property. RR No is a stored attribute.
- A bill is unique on (`account_id`, `bill_period_start`).

### 4.6 Evaluation tab
- Run on **all 100 sample bills**, generated with a fixed seed (42) and split across clean (35), medium (35) and noisy (30) bills. Uploaded bills stay outside this set (judge-only, see 4.8).
- Shows Gemini extraction vs ground truth, and LLM-judge verdicts vs ground truth, per field and per bill.
- Workflow: scan each bill on the Bills tab, then press Judge on the Evaluation tab for that bill. Both steps are one bill at a time; there is no bulk job.
- Shows the metrics in section 7.
- Per-field exact match is explicitly extractor vs ground truth. It lists the 10 lowest-matching fields by default, with an option to show all 33.
- The judge result is shown as a confusion matrix (rows: extractor wrong / right; columns: judge incorrect / correct / no verdict), with precision and recall below it.
- Short definitions are shown for arithmetic consistency, noise level and "fields without verdict".
- The existing field-mismatch and judge-disagreement tables are kept as they are.

### 4.8 Upload tab
**Purpose:** store a bill file only. Upload never calls Gemini. Extraction stays a manual Scan on the Bills tab.

**Form**
- City: dropdown of existing cities, plus "Add new" (type a new value and press Enter).
- Property name and Owner: dropdowns narrowed by city, each with "Add new". Sample and uploaded properties are both listed. A new property creates a minimal master record (placeholder account ID `UPL-n`; the real account ID shows after a scan).
- Bill month-year: month and year pickers. Stored as the first day of the month.
- File: jpg, jpeg, png or pdf, max 2 MB. PDF: first page only, rendered to an image.
- Banner: "Don't upload real bills. This app has no login."

**Two layers of file quality checking.** Layer 1 runs at upload (below, no model call) and rejects unusable files before they are stored as bills. Layer 2 runs at scan time: the 11-field completeness flag.

**Checks, in order (no model call)**
1. Size is 2 MB or less, and the file is not empty.
2. Content check: the file's first bytes must match JPEG, PNG or PDF. The type is taken from the content, not the extension. Mismatch is rejected.
3. The file must actually open: the image decodes, or the PDF opens, is not encrypted and has at least 1 page. A pixel cap guards against decompression bombs.
4. Quality gate: minimum resolution and a blur score. Initial thresholds (config values, tunable): short side at least 500 px; blur score (variance of the Laplacian after resizing to 1000 px width) at least 8; at most 25 million pixels. Chosen from the first 20 sample bills (600 to 740 px wide, scores 11 to 665) and blurred copies of them (heavy blur scores below 3).
5. Duplicate check on city + property + owner + month-year (see below).
- A file failing 2 to 4 is rejected. It is kept and logged as Failure with a reason (e.g. "Too blurry", "Resolution too low", "Not a valid PDF").

**Duplicates**
- On a match, a dialog asks "A bill already exists for <property>, <month-year>. Replace it?" with Replace and Cancel.
- Replace overwrites the image and deletes the old extraction and judge verdicts. The bill returns to "Not scanned".
- The old history row is marked "Replaced". Every upload keeps its own history row.

**Progress and lock**
- While an upload runs, the tab is greyed with a progress bar and real stages: Uploading, Validating, Quality check, Saving. No action is allowed on the tab.
- One upload at a time (server-side lock as well).
- The work runs on the server once the file is received. Closing the browser during the transfer saves nothing and creates no row. After receipt, processing normally completes without the browser. A row still Pending after 10 minutes (e.g. server restart) becomes Failure ("Interrupted").

**History table** (newest first)
- Columns: Uploaded date, City, Property, Owner, Bill month-year, File name, Status (Success / Failure / Pending / Replaced), Reason, View, Retry.
- View (Success only): jumps to the Bills tab, filtered to that bill and expanded, to scan it and see extracted fields.
- Retry (Failure only): re-runs the whole pipeline on the kept file (checks, quality gate, save). It covers technical failures (database error, timeout, interrupted upload) as well as threshold changes. The file is kept from the moment it is received, so Retry works even if the failure happened before the checks. To fix a genuinely bad file, upload a new one.

**After upload**
- The bill appears on the Bills tab as "Not scanned", with the bill image visible. Reading period stays blank until scanned (same rule as sample bills).
- Scanning an uploaded bill counts toward the Gemini daily cap. If any of the 11 critical fields is missing after a scan, the bill shows "Unclear: n of 11 critical fields missing". The extraction is still stored.
- Backend only: `bills.source` = sample or upload. No Source column in the UI.

**Judge and Evaluation for uploads**
- No ground truth. The judge (manual, as today) checks the extracted values against the bill image and gives correct / incorrect per field.
- Evaluation gets a separate section "Uploaded bills (judge-estimated)": bills scanned, bills judged, % fields judged correct, % bills with all 11 critical fields judged correct, 11-field completeness %, fields without verdict.
- Uploads are excluded from ground-truth accuracy, judge-vs-truth agreement and noise splits.

**Out of scope:** deleting uploads, multi-page PDFs, authentication, extraction at upload time.

**Open point:** initial quality thresholds (see Q&A below).

## 5. Technical requirements
- **Database:** Neon Postgres is the only data store. Images are stored in Neon as `bytea` in their own table, and are never loaded for list views. No blob storage.
- **Extraction model:** `gemini-3.5-flash-lite` (Gemini free tier). Verified limits: 15 requests/min, 500 requests/day, 250,000 input tokens/min. The model id is pinned, never an alias, and kept as a config value. Output is JSON against a fixed schema. Retry with backoff on 429.
- **Judge model:** `mistral-large-4` (Mistral free plan). Verified limits: 20,000 tokens/min, 0.83 requests/sec. Pin the exact id, never a `-latest` alias. The judge sees the bill image and the extracted JSON, and returns a verdict per field.
- **Frontend:** NiceGUI hosted on Render (free tier).
- **Seeding:** on first start in deployed mode, if the database is empty, the app loads the bundled `seed_data/` (100 images, ground truth, owners, properties). From v0.2 the seeder is a top-up: see 3a. Local mode never writes.
- **Read-only local:** extraction and DB writes work only in the deployed app. Local runs are read-only, and the Gemini and Mistral keys exist only as Render environment variables.
- **Pacing:** UI scans are one at a time, well inside the limits. The 100-bill evaluation run is paced to stay under 15 extractor requests/min and the judge's 20,000 tokens/min.
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
| `uploads` | upload_id, bill_id (null until success), city, property_name, owner, bill_month, file_name, mime, size_bytes, status (pending/success/failure/replaced), reason, uploaded_at, replaced_at, file (`bytea`, kept for non-success rows; the image of a successful upload lives in `bill_images`) |
| `eval_results` | bill_id, field, truth, extracted, extractor_match, judge_verdict, judge_agrees_with_truth |

Note: extracted data is overwritten on re-scan, but `extraction_runs` stays append-only so usage and latency metrics are not lost.

## 7. Metrics
- **Usage:** input and output tokens per call and in total, with the equivalent paid cost.
- **Latency:** average, median, P90, P95, max, for extractor and judge.
- **Reliability:** error, 429 and retry rate; valid-JSON-against-schema rate.
- **Extraction accuracy (vs ground truth):** per-field exact match, critical fields reported separately; bill-level accuracy (all critical fields correct); arithmetic-consistency pass rate.
- **Judge quality:** judge vs ground-truth agreement (precision/recall on "field is wrong"), and a list of disagreements.
- **Breakdown:** all of the above split by noise level.

### 4.9 Key Q&A and decisions (Upload tab)
| Question | Decision |
|---|---|
| Upload extracts data with Gemini? | No. Upload only stores the file. Extraction stays a manual Scan, so the Gemini cap is not used by upload. |
| Property and owner entry | Dropdown of existing uploaded values, plus "Add new". A new property creates a minimal master record. |
| Bill period input | Month-year. Duplicate key = city + property + owner + month-year. |
| Max size, PDF pages | 2 MB. PDF first page only. |
| How is content validated? | First bytes must match JPEG, PNG or PDF; the file must open (image decodes; PDF not encrypted, at least 1 page). Type comes from content, not extension. |
| Is file quality checked at upload? | Yes. Resolution and blur gate at upload rejects unclear files. Separately, at scan time a bill with any of the 11 critical fields missing shows "Unclear: n of 11 missing" (extraction still stored). |
| Rejected files | Kept, status Failure with a reason. |
| Duplicates | Prompt Replace / Cancel. Replace overwrites image, extraction and verdicts. Old history row marked Replaced. |
| Replacing a sample bill | Allowed ("Allow replacement"). The bill becomes an uploaded bill: its noise level is cleared and it no longer counts in ground-truth metrics (the ground truth no longer matches the new image). The Replace prompt warns about this. |
| Source column in UI? | No. `bills.source` exists in the backend only. |
| Evaluation of uploads | No ground truth. The judge checks extracted values against the image; shown in a separate judge-estimated section. |
| Progress and lock | Grey overlay, progress bar, real stages, no actions, one upload at a time. |
| Browser closed mid-upload | During transfer: nothing saved, no row. After receipt: server finishes the work. Pending rows older than 10 minutes become Failure (Interrupted). |
| Retry | Kept. Re-runs the whole pipeline on the kept file; also covers technical failures. |
| Privacy | No login accepted. Banner: "Don't upload real bills." |
| Thresholds | Picked by Claude: short side at least 500 px, blur score at least 8, at most 25 MP. Tunable config; revisit with real uploads. |
| Implementation notes | The "Uploading" stage covers the file transfer and keeping the file on the history row. Pending rows older than 10 minutes are failed when the history loads. Time is shown in IST. Thresholds can be overridden with `UPLOAD_MIN_SHORT_SIDE`, `UPLOAD_MIN_BLUR_SCORE`, `UPLOAD_MAX_PIXELS`. |
| Tab order | Bills, Upload, Extracted data, Evaluation, Field guide. |


### 7a. Upload evals
- **Quality gate:** build a labelled set by degrading the sample bills (first 20 are enough) (blur, low resolution, dark, crop). Report false-reject rate on good files and miss rate on bad ones. Calibrate thresholds from it.
- **Validation:** unit cases for wrong extension, corrupt file, encrypted or empty PDF, oversize, multi-page PDF, huge pixel count.
- **Duplicates:** replace and cancel leave exactly one bill, no orphan extraction or verdicts, correct history statuses.
- **Reliability and latency:** success / failure / interrupted counts, failure reasons, p50 / p90 per stage (render, quality check, save).
- **Post-scan quality:** 11-field completeness rate and judge-estimated field accuracy on uploaded bills.
- **Human spot-check:** 10 uploaded bills reviewed by you against the image.

## 8. Risks and open items
- **Deferred: ground truth is not independent of the images.** Today the generator creates each bill's values first, writes them to the ground-truth JSON, and renders the image from the same values. That is acceptable for this synthetic 100-bill evaluation but does not scale or reflect real bills. Later options: human-verified labels on real bills, or a separately keyed ground-truth source. Revisit before any real-bill or larger-scale evaluation.
- **Judge strength:** a judge weaker than the extractor may miss real errors. Large 4 is Mistral's largest current multimodal model, and it is a different model family from the extractor. Judge-vs-ground-truth agreement is therefore a required result, and we should be ready to swap the judge model.
- **Uploads:** bill files are stored in Neon (2 MB each; failures are kept too). No login means anyone with the URL can upload and view. Judge-estimated accuracy on uploads is not ground truth.
- **Free-tier terms:** limits are verified (section 5). A third-party tracker reports that Mistral free-plan data may be used for model improvement unless opted out; confirm in your Mistral account settings. Gemini free-tier content may be used to improve Google's products, so no real bills.
- **Render/Neon cold starts:** verify current free-tier sleep behaviour against the latency requirement.
- **"Scan" wording:** interpreted as running extraction on an existing stored image, with no new image generated.

## Implementation addenda (post-review)
- `extractions.raw_json` stores the model's raw output; the judge sees raw values, not the normalised ones.
- Daily guard `GEMINI_DAILY_CAP`; single in-flight provider call; retries with backoff on 429/5xx/network, honouring Retry-After.
- Arithmetic consistency is reported as pass/fail/not-checkable (null inputs are not failures).
- Evaluation adds per-bill and field-mismatch tables. Authentication remains out of scope.
- Evaluation info (i) icons explain Critical fields match (lists the 11 fields), Arithmetic-consistent bills (the 5 checks and non-checkable bills) and Fields without verdict.
- Speed: reads are cached in memory (cleared on every write; 120 s TTL as a safety net), the extracted list query skips `raw_json`, the pool keeps one warm connection, the cache is warmed at startup, and the Extracted data, Evaluation and Field guide tabs are built on first open. Queries or page loads over 0.5 s are logged as warnings.
