"""NiceGUI pages: Bills, Extracted data, Evaluation, Field guide."""
import asyncio
import inspect
import logging
import time
from datetime import date, timedelta, timezone

from fastapi import Response
from nicegui import app as nicegui_app, run, ui

from app import cascade, db, export, metrics, services, uploads
from app.config import get_settings
from app.fields import KEYS, LABELS, MEANINGS, PRIMARY

IST = timezone(timedelta(hours=5, minutes=30))
MONTHS = {i: n for i, n in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

log = logging.getLogger("billsense.ui")
IDENT_COLS = {"city": "City", "owner": "Owner", "property_name": "Property name"}
CRITICAL_LIST = ", ".join(LABELS[k] for k in PRIMARY)
INFO = {
    "Critical fields match (%)": f"Share of the {len(PRIMARY)} critical fields that match ground truth: {CRITICAL_LIST}.",
    "Arithmetic-consistent bills (%), of checkable":
        "A bill passes if all 5 checks hold on the extracted values: (1) present - previous reading = units; "
        "(2) units x rate = energy charges; (3) tax = 9% of fixed + energy + FPPCA; (4) charges add up to current demand; "
        "(5) current demand + debit/credit + arrears = net payable. Bills missing a needed value are not checkable "
        "and are left out of the percentage.",
    "Fields judged correct (%)": "Share of judged fields the judge marked correct. There is no ground truth for uploaded bills, so this is the judge's opinion, not a measured accuracy.",
    "Bills with all critical fields judged correct (%)": "Of judged uploaded bills, share where the judge marked all 11 critical fields correct.",
    "Bills with all critical fields read (%)": "Of scanned uploaded bills, share where all 11 critical fields were extracted (none missing).",
    "fields without verdict": "The judge skipped the field or answered something other than correct/incorrect. "
                              "Counts as a miss only if the extractor was actually wrong on that field.",
}


@nicegui_app.get("/bill_image/{bill_id}")
async def bill_image(bill_id: str):
    row = await run.io_bound(db.get_image, bill_id)
    if not row:
        return Response(status_code=404)
    return Response(content=bytes(row["image"]), media_type=row["mime"],
                    headers={"Cache-Control": "private, max-age=3600"})


def _fmt(v):
    if v is None:
        return "-"
    if hasattr(v, "as_tuple"):          # Decimal from NUMERIC
        v = float(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _kv(fields: dict, keys):
    return [(LABELS[k], _fmt(fields.get(k))) for k in keys]


class DbDown(Exception):
    pass


async def _load(fn, *a):
    t0 = time.monotonic()
    try:
        return await run.io_bound(fn, *a)
    except Exception as e:                                   # noqa: BLE001 - Neon waking up, network, etc.
        raise DbDown(str(e)) from e
    finally:
        dt = time.monotonic() - t0
        if dt > 0.5:
            log.warning("slow load %.2fs: %s", dt, getattr(fn, "__name__", "?"))


def _bill_rows():
    """Bills joined with what the Bills tab needs: scan label and bill reading period (month-year of the
    extracted bill period end; blank until the bill is scanned)."""
    missing = {r["bill_id"]: sum(r[k] in (None, "") for k in PRIMARY) for r in db.extracted_rows()}
    out = []
    for b in db.bills():
        scanned = b["scan_status"] == "scanned"
        n = missing.get(b["bill_id"], 0)
        flag = f"Unclear: {n} of {len(PRIMARY)} missing" if scanned and b["source"] == "upload" and n else ""
        out.append({**b, "scanned": scanned, "scan_label": "Scanned" if scanned else "Not scanned", "flag": flag,
                    "reading_period": cascade.month_year(b["extracted_period_end"]) if scanned else ""})
    return out


def _eval_inputs():
    return db.all_runs(), db.all_eval(), db.extracted_rows(), db.judged_bills(), db.eval_rows_all()


def _bills_and_eval():
    return _bill_rows(), db.eval_rows_all()


def db_down_notice(refresh):
    ui.label("The database is waking up or unavailable. Wait a few seconds, then retry.").classes("text-negative")
    ui.button("Retry", on_click=refresh).props("outline size=sm")


@ui.page("/", response_timeout=30)
async def index():
    s = get_settings()
    ui.page_title("Electricity bill extraction")
    with ui.row().classes("items-center gap-4"):
        ui.label("Electricity bill extraction").classes("text-xl font-medium")
        if not s.writes_enabled:
            ui.badge("Read-only (local mode): scanning and judging are disabled", color="orange")
    if not s.database_url:
        ui.label("DATABASE_URL is not set.").classes("text-negative")
        return

    try:
        all_rows = await _load(_bill_rows)
    except DbDown:
        db_down_notice(lambda: ui.navigate.reload())
        return
    state = {**{n: None for n in cascade.COLS}, "open": set()}

    with ui.tabs().classes("w-full") as tabs:
        t_bills = ui.tab("Bills")
        t_up = ui.tab("Upload")
        t_data = ui.tab("Extracted data")
        t_eval = ui.tab("Evaluation")
        t_guide = ui.tab("Field guide")

    # ---------------- Bills tab ----------------
    def _detail(bid, ev_rows):
        """Per-field comparison shown when a scanned bill is expanded (extracted vs ground truth, judge verdict)."""
        by_field = {r["field"]: r for r in ev_rows}
        out = []
        for k in KEYS:
            r = by_field.get(k)
            if not r:
                continue
            verdict = r["judge_verdict"]
            out.append({"k": k, "f": LABELS[k], "p": k in PRIMARY,
                        "e": "-" if r["extracted"] is None else r["extracted"],
                        "t": "-" if r["truth"] is None else r["truth"],
                        "m": None if r["extractor_match"] is None else bool(r["extractor_match"]),
                        "j": "" if verdict is None else ("no verdict" if verdict == "missing" else verdict)})
        return out

    @ui.refreshable
    async def bills_table():
        try:
            all_bills, ev = await _load(_bills_and_eval)
        except DbDown:
            return db_down_notice(bills_table.refresh)
        ev_by_bill = {}
        for r in ev:
            ev_by_bill.setdefault(r["bill_id"], []).append(r)
        rows = []
        for b in cascade.filter_bills(all_bills, state):
            scanned = b["scan_status"] == "scanned"
            rows.append({"bill_id": b["bill_id"], "account_id": (b["extracted_account_id"] or "") if scanned else "",
                         "property": b["property_name"], "city": b["city"], "owner": b["owner"],
                         "period": b["reading_period"],
                         "status": b["scan_label"], "flag": b["flag"], "scanned": scanned, "noise": b["noise_level"],
                         "detail": _detail(b["bill_id"], ev_by_bill.get(b["bill_id"], [])) if scanned else [],
                         "_open": b["bill_id"] in state["open"], "_all": False, "_mm": False, "_zoom": False})
        cols = [{"name": "account_id", "label": "Account ID", "field": "account_id", "align": "left"},
                {"name": "property", "label": "Property", "field": "property", "align": "left"},
                {"name": "city", "label": "City", "field": "city", "align": "left"},
                {"name": "owner", "label": "Owner", "field": "owner", "align": "left"},
                {"name": "period", "label": "Bill reading period", "field": "period", "align": "left"},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
                {"name": "action", "label": "", "field": "action"}]
        t = ui.table(columns=cols, rows=rows, row_key="bill_id").classes("w-full")
        t.add_slot("header", """<q-tr :props="props"><q-th auto-width />
            <q-th v-for="col in props.cols" :key="col.name" :props="props">{{ col.label }}</q-th></q-tr>""")
        t.add_slot("body", r"""
            <q-tr :props="props">
              <q-td auto-width><q-btn size="sm" flat round dense :icon="props.row._open ? 'expand_more' : 'chevron_right'"
                    @click="props.row._open = !props.row._open" /></q-td>
              <q-td v-for="col in props.cols" :key="col.name" :props="props">
                <q-btn v-if="col.name === 'action'" size="sm" dense outline :label="props.row.scanned ? 'Re-scan' : 'Scan'"
                       @click.stop="() => $parent.$emit('scan', props.row.bill_id)" />
                <span v-else-if="col.name === 'status'"><q-badge :color="props.row.scanned ? 'positive' : 'grey'"
                       :label="col.value" /><q-badge v-if="props.row.flag" color="orange" class="q-ml-xs" :label="props.row.flag" /></span>
                <span v-else>{{ col.value }}</span>
              </q-td>
            </q-tr>
            <q-tr v-if="props.row._open" :props="props">
              <q-td colspan="100%" class="bg-grey-1">
                <div class="row no-wrap q-gutter-md q-pa-sm">
                  <div style="width:260px;flex:none">
                    <img :src="'/bill_image/' + props.row.bill_id" style="width:100%;cursor:zoom-in;border:1px solid #ccc"
                         @click="props.row._zoom = true" />
                    <div class="text-caption text-grey q-mt-xs">{{ props.row.bill_id }}<span v-if="props.row.noise"> · noise: {{ props.row.noise }}</span> · click image to enlarge</div>
                    <q-dialog v-model="props.row._zoom"><img :src="'/bill_image/' + props.row.bill_id"
                         style="max-width:92vw;max-height:92vh;background:white" /></q-dialog>
                  </div>
                  <div style="flex:1;min-width:0">
                    <div v-if="!props.row.scanned" class="text-grey">Not scanned yet. Press Scan on this row.</div>
                    <div v-else>
                      <table style="width:100%;table-layout:fixed;border-collapse:collapse;font-size:13px">
                        <colgroup><col style="width:20%" /><col style="width:30%" /><col style="width:30%" /><col style="width:8%" /><col style="width:12%" /></colgroup>
                        <thead><tr><th class="text-left" style="height:auto;padding:2px 6px">Field</th><th class="text-left" style="height:auto;padding:2px 6px">Extracted</th>
                          <th class="text-left" style="height:auto;padding:2px 6px">Ground truth</th><th class="text-left" style="height:auto;padding:2px 6px">Match</th><th class="text-left" style="height:auto;padding:2px 6px">Judge</th></tr></thead>
                        <tbody>
                          <tr v-for="d in props.row.detail.filter(d => (props.row._all || d.p) && (!props.row._mm || d.m === false || d.j === 'incorrect'))"
                              :key="d.k" :class="(d.m === false || d.j === 'incorrect') ? 'bg-red-1' : ''">
                            <td style="padding:2px 6px;height:auto;white-space:normal;overflow-wrap:anywhere">{{ d.f }}</td><td style="padding:2px 6px;height:auto;white-space:normal;overflow-wrap:anywhere">{{ d.e }}</td><td style="padding:2px 6px;height:auto;white-space:normal;overflow-wrap:anywhere">{{ d.t }}</td>
                            <td style="padding:2px 6px;height:auto"><q-icon v-if="d.m === true" name="check" color="positive" /><q-icon v-else-if="d.m === false" name="close" color="negative" /><span v-else>-</span></td>
                            <td style="padding:2px 6px;height:auto">{{ d.j }}</td>
                          </tr>
                        </tbody>
                      </table>
                      <div class="q-mt-sm text-caption text-grey">
                        Showing {{ props.row.detail.filter(d => (props.row._all || d.p) && (!props.row._mm || d.m === false || d.j === 'incorrect')).length }}
                        of {{ props.row.detail.length }} fields
                        <q-btn flat dense size="sm" no-caps :label="props.row._all ? 'Primary fields only' : 'Show all fields'"
                               @click="props.row._all = !props.row._all" />
                        <q-btn flat dense size="sm" no-caps :label="props.row._mm ? 'Show all rows' : 'Mismatches only'"
                               @click="props.row._mm = !props.row._mm" />
                      </div>
                    </div>
                  </div>
                </div>
              </q-td>
            </q-tr>""")
        t.on("scan", lambda e: do_scan(e.args))
        if not rows:
            ui.label("No bills match the selected filters.").classes("text-grey")

    async def do_scan(bid):
        if not s.writes_enabled:
            ui.notify("Read-only mode: scanning only works in the deployed app.", type="warning")
            return
        n = ui.notification(f"Scanning {bid}…", spinner=True, timeout=None)
        try:
            res = await run.io_bound(services.scan_bill, bid)
        except Exception as e:                                  # noqa: BLE001
            res = {"ok": False, "message": f"Scan failed: {e}"}
        n.dismiss()
        ui.notify(res["message"], type="positive" if res["ok"] else "negative")
        state["open"].add(bid)
        try:
            rows = await _load(_bill_rows)
            state.update(cascade.normalise(rows, state))
            sync_selects(rows)                                  # a new scan can add a reading-period option
        except DbDown:
            pass
        bills_table.refresh()

    def sync_selects(rows):
        opts = cascade.options(rows, state)
        for n, sel in selects.items():
            sel.options = opts[n]
            sel.value = state[n]
            sel.update()

    def on_filter(name):
        async def handler(e):
            if e.value == state[name]:                          # programmatic sync, nothing changed
                return
            state[name] = e.value
            try:
                rows = await _load(_bill_rows)
            except DbDown:
                return
            state.update(cascade.normalise(rows, state, keep=name))
            sync_selects(rows)
            bills_table.refresh()
        return handler

    selects = {}
    lazy = {}                                                    # tab name -> (container, refreshable or builder)
    built = set()
    with ui.tab_panels(tabs, value=t_bills, on_change=lambda e: on_tab(e.value)).classes("w-full") as panels:
        with ui.tab_panel(t_bills):
            opts = cascade.options(all_rows, state)
            with ui.row().classes("w-full gap-4"):
                for name, label in (("city", "City"), ("owner", "Owner"), ("property", "Property name"),
                                    ("period", "Bill reading period"), ("status", "Scan status")):
                    selects[name] = ui.select(opts[name], label=label, value=None, clearable=True,
                                              on_change=on_filter(name)).classes("w-56")
            await bills_table()

        # ---------------- Upload tab (built on first open) ----------------
        with ui.tab_panel(t_up):
            up_box = ui.column().classes("w-full")

            async def upload_panel():
                try:
                    plist = await _load(db.properties)
                except DbDown:
                    return db_down_notice(lambda: ui.navigate.reload())
                ui.label("Don't upload real bills. This app has no login.").classes(
                    "text-caption text-orange-9 bg-orange-1 q-pa-xs rounded-borders")
                up = {"meta": None, "replace": False, "waiting": False}
                this_year = date.today().year

                def city_opts():
                    return sorted({p["city"] for p in plist})

                def narrowed(key):
                    c = city_sel.value
                    return sorted({p[key] for p in plist if not c or p["city"] == c})

                def on_city(e):
                    prop_sel.options = narrowed("property_name"); prop_sel.update()
                    owner_sel.options = narrowed("owner"); owner_sel.update()

                with ui.row().classes("w-full items-start gap-4"):
                    city_sel = ui.select(city_opts(), label="City", with_input=True, new_value_mode="add-unique",
                                         clearable=True, on_change=on_city).classes("w-56")
                    prop_sel = ui.select(narrowed("property_name"), label="Property name", with_input=True,
                                         new_value_mode="add-unique", clearable=True).classes("w-56")
                    owner_sel = ui.select(narrowed("owner"), label="Owner", with_input=True,
                                          new_value_mode="add-unique", clearable=True).classes("w-56")
                    month_sel = ui.select(MONTHS, label="Bill month", clearable=True).classes("w-32")
                    year_sel = ui.select([this_year - 2, this_year - 1, this_year], label="Year", clearable=True).classes("w-28")
                ui.label("Add new: type a new city, property or owner and press Enter.").classes("text-caption text-grey")

                # --- progress overlay: the whole screen is greyed and nothing else can be clicked
                with ui.dialog().props("persistent") as ov, ui.card().classes("w-96"):
                    ov_title = ui.label("Uploading").classes("text-base font-medium")
                    bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
                    ov_stage = ui.label("").classes("text-caption text-grey")

                def stage(i, n, label):
                    ov_title.text = f"Uploading {up['meta']['file_name']}" if up["meta"] else "Uploading"
                    ov_stage.text = f"{label}…"
                    bar.value = i / n
                    if not ov.value:
                        ov.open()

                async def run_job(job):
                    steps = job.steps()
                    try:
                        with uploads.exclusive():
                            up["meta"] = job.meta
                            result = None
                            try:
                                for i, (label, fn) in enumerate(steps):
                                    stage(i, len(steps), label)
                                    await run.io_bound(fn)
                                bar.value = 1.0
                                result = job.done()
                            except Exception as e:                      # noqa: BLE001 - Rejected or technical
                                result = await run.io_bound(job.fail, e)
                    except uploads.Busy as e:
                        result = {"ok": False, "message": str(e)}
                    finally:
                        up["waiting"] = False
                        ov.close()
                    ui.notify(result["message"] if not result["ok"] else f'{result["message"]}. Open it on the Bills tab to scan it.',
                              type="positive" if result["ok"] else "negative")
                    uploader.reset()
                    upload_history.refresh()
                    try:
                        rows = await _load(_bill_rows)
                        state.update(cascade.normalise(rows, state))
                        sync_selects(rows)
                        bills_table.refresh()
                    except DbDown:
                        pass

                async def confirm_replace(dup):
                    note = " It is a sample bill: after replacing it no longer counts in the accuracy metrics." if dup["source"] == "sample" else ""
                    with ui.dialog().props("persistent") as d, ui.card():
                        ui.label("Replace existing bill?").classes("text-base font-medium")
                        ui.label("A bill for this city, property, owner and month already exists. Replacing it deletes its "
                                 "extracted data and judge results." + note).classes("w-96")
                        with ui.row():
                            ui.button("Replace", on_click=lambda: d.submit(True)).props("no-caps")
                            ui.button("Cancel", on_click=lambda: d.submit(False)).props("outline no-caps")
                    res = await d
                    d.delete()
                    return bool(res)

                async def on_file(e):
                    """The file has reached the server: run the checks and save."""
                    meta = up["meta"]
                    if meta is None:
                        return
                    data = await e.file.read()
                    job = uploads.Job(meta["city"], meta["property"], meta["owner"], meta["month"], e.file.name, data,
                                      replace=up["replace"])
                    await run_job(job)

                def on_rejected(e):
                    up["waiting"] = False
                    ov.close()
                    ui.notify("File rejected: use a jpg, png or pdf of 2 MB or less.", type="negative")

                uploader = ui.upload(label="Choose a bill: jpg, png or pdf, max 2 MB", auto_upload=False, max_files=1,
                                     max_file_size=uploads.MAX_BYTES, on_upload=on_file, on_rejected=on_rejected
                                     ).props('accept=".jpg,.jpeg,.png,.pdf" flat bordered hide-upload-btn').classes("w-full max-w-xl")

                async def on_submit():
                    if not s.writes_enabled:
                        ui.notify("Read-only mode: uploading only works in the deployed app.", type="warning")
                        return
                    city, prop, owner = (uploads.clean(x.value) for x in (city_sel, prop_sel, owner_sel))
                    if not (city and prop and owner and month_sel.value and year_sel.value):
                        ui.notify("Enter city, property name, owner, bill month and year.", type="warning")
                        return
                    n = await ui.run_javascript(f"getElement({uploader.id}).$refs.qRef.queuedFiles.length")
                    if not n:
                        ui.notify("Choose a file first.", type="warning")
                        return
                    month = date(int(year_sel.value), int(month_sel.value), 1)
                    try:
                        dup = await _load(uploads.find_duplicate, city, prop, owner, month)
                    except DbDown:
                        ui.notify("Database unavailable. Try again shortly.", type="negative")
                        return
                    up["replace"] = False
                    if dup:
                        if not await confirm_replace(dup):
                            return
                        up["replace"] = True
                    up["meta"] = {"city": city, "property": prop, "owner": owner, "month": month, "file_name": "file"}
                    up["waiting"] = True
                    stage(0, 4, "Uploading")
                    uploader.run_method("upload")
                    def watchdog():                                       # the transfer never arrived
                        if up["waiting"]:
                            up["waiting"] = False
                            ov.close()
                            ui.notify("Upload did not complete. Try again.", type="negative")
                    ui.timer(60, watchdog, once=True)

                ui.button("Upload", on_click=on_submit).props("no-caps")

                # --- history
                async def retry(upload_id):
                    if not s.writes_enabled:
                        ui.notify("Read-only mode: uploading only works in the deployed app.", type="warning")
                        return
                    try:
                        job = await run.io_bound(uploads.Job.from_upload, upload_id)
                    except uploads.Rejected as e:
                        ui.notify(str(e), type="negative")
                        return
                    dup = await _load(db.find_bill, job.meta["city"], job.meta["property"], job.meta["owner"], job.meta["month"])
                    if dup:
                        if not await confirm_replace(dup):
                            return
                        job.replace = True
                    await run_job(job)

                async def view(row):
                    rows = await _load(_bill_rows)
                    state.update({"city": row["city"], "owner": row["owner"], "property": row["property"],
                                  "period": None, "status": None})
                    state["open"].add(row["bill_id"])
                    state.update(cascade.normalise(rows, state, keep="property"))
                    sync_selects(rows)
                    bills_table.refresh()
                    tabs.set_value(t_bills)

                @ui.refreshable
                async def upload_history():
                    ui.label("Upload history").classes("text-base font-medium mt-4")
                    try:
                        if s.writes_enabled:
                            await _load(db.fail_stale_uploads)
                        rows_db = await _load(db.uploads_list)
                    except DbDown:
                        return db_down_notice(upload_history.refresh)
                    if not rows_db:
                        ui.label("No uploads yet.").classes("text-grey")
                        return
                    label = {"success": "Success", "failure": "Failure", "pending": "Pending", "replaced": "Replaced"}
                    rows = [{"upload_id": r["upload_id"], "bill_id": r["bill_id"],
                             "uploaded": r["uploaded_at"].astimezone(IST).strftime("%d %b %Y %H:%M"),
                             "city": r["city"], "property": r["property_name"], "owner": r["owner"],
                             "month": f'{r["bill_month"]:%b %Y}', "file": r["file_name"],
                             "status": label.get(r["status"], r["status"]), "reason": r["reason"] or "",
                             "action": r["status"]} for r in rows_db]
                    cols = [{"name": c, "label": c.capitalize() if c != "action" else "", "field": c, "align": "left"}
                            for c in ("uploaded", "city", "property", "owner", "month", "file", "status", "reason", "action")]
                    t = ui.table(columns=cols, rows=rows, row_key="upload_id").classes("w-full").props("dense flat bordered")
                    t.add_slot("body-cell-status", """<q-td :props="props"><q-badge :label="props.value"
                        :color="{Success:'positive',Failure:'negative',Pending:'orange',Replaced:'grey'}[props.value]" /></q-td>""")
                    t.add_slot("body-cell-action", """<q-td :props="props">
                        <q-btn v-if="props.row.action === 'success'" size="sm" dense flat no-caps label="View"
                               @click.stop="() => $parent.$emit('view', props.row)" />
                        <q-btn v-if="props.row.action === 'failure'" size="sm" dense outline no-caps label="Retry"
                               @click.stop="() => $parent.$emit('retry', props.row.upload_id)" /></q-td>""")
                    t.on("view", lambda e: view(e.args))
                    t.on("retry", lambda e: retry(e.args))

                await upload_history()
                up_box.refresh_history = upload_history.refresh

            lazy["Upload"] = (up_box, upload_panel, lambda: up_box.refresh_history())

        # ---------------- Extracted data tab (built on first open) ----------------
        with ui.tab_panel(t_data):
            data_box = ui.column().classes("w-full")
            data_state = {"filters": [], "cols": list(PRIMARY)}

            @ui.refreshable
            async def data_table():
                try:
                    rows_db = await _load(db.extracted_rows)
                except DbDown:
                    return db_down_notice(data_table.refresh)
                if not rows_db:
                    ui.label("No bills have been scanned yet.").classes("text-grey")
                    return
                all_data = []
                for r in rows_db:
                    f = db.extraction_fields(r)
                    row = {"bill_id": r["bill_id"], "city": r["city"], "owner": r["owner"], "property_name": r["property_name"]}
                    row.update({k: _fmt(f.get(k)) for k in KEYS})
                    all_data.append(row)
                names = {**IDENT_COLS, **LABELS}
                rows = [r for r in all_data
                        if all(txt.lower() in str(r[k]).lower() for k, txt in data_state["filters"])]
                holder = {}

                # --- filter bar: keep rows whose field value contains the text
                with ui.row().classes("items-end gap-2"):
                    fsel = ui.select(names, label="Filter field", value="account_id").classes("w-48")
                    ftxt = ui.input("contains").classes("w-48")

                    def add_filter():
                        if ftxt.value and ftxt.value.strip():
                            data_state["filters"].append((fsel.value, ftxt.value.strip()))
                            data_table.refresh()
                    ui.button("Add filter", on_click=add_filter).props("outline no-caps size=sm")
                    for i, (k, txt) in enumerate(data_state["filters"]):
                        def remove(e, i=i):
                            if not e.value:
                                data_state["filters"].pop(i)
                                data_table.refresh()
                        ui.chip(f"{names[k]} contains '{txt}'", removable=True, on_value_change=remove)

                # --- column chooser + CSV download
                def shown():
                    chosen = set(data_state["cols"])
                    return list(IDENT_COLS) + [k for k in KEYS if k in chosen]

                def on_cols(e):
                    data_state["cols"] = list(e.value or [])
                    holder["t"]._props["visible-columns"] = shown()
                    holder["t"].update()

                def on_select():
                    n = len(holder["t"].selected)
                    btn.set_text(f"Download CSV ({n} selected)")
                    btn.set_enabled(n > 0)

                def download():
                    ids = [r["bill_id"] for r in holder["t"].selected]
                    ui.download.content(export.extracted_csv(rows_db, ids), "billsense_extracted.csv", "text/csv")

                with ui.row().classes("items-center gap-4"):
                    ui.select({k: LABELS[k] for k in KEYS}, multiple=True, value=data_state["cols"], label="Columns",
                              on_change=on_cols).props("use-chips dense").classes("w-96")
                    btn = ui.button("Download CSV (0 selected)", on_click=download).props("outline no-caps size=sm")
                    btn.set_enabled(False)
                ui.label(f"{len(rows)} of {len(all_data)} bills. Tick rows (or the header box for all) to download them as CSV "
                         "with all 33 fields.").classes("text-caption text-grey")

                cols = ([{"name": k, "label": lbl, "field": k, "align": "left"} for k, lbl in IDENT_COLS.items()]
                        + [{"name": k, "label": LABELS[k], "field": k, "align": "left"} for k in KEYS])
                t = ui.table(columns=cols, rows=rows, row_key="bill_id", selection="multiple",
                             on_select=on_select).classes("w-full").props("dense")
                t._props["visible-columns"] = shown()
                holder["t"] = t
                if not rows:
                    ui.label("No scanned bills match the filters.").classes("text-grey")
            lazy["Extracted data"] = (data_box, data_table, data_table.refresh)

        # ---------------- Evaluation tab ----------------
        with ui.tab_panel(t_eval):
            eval_box = ui.column().classes("w-full")

            async def do_judge(bid):
                if not s.writes_enabled:
                    ui.notify("Read-only mode: judging only works in the deployed app.", type="warning")
                    return
                n = ui.notification(f"Judging {bid}…", spinner=True, timeout=None)
                try:
                    res = await run.io_bound(services.judge_bill, bid)
                except Exception as e:                          # noqa: BLE001
                    res = {"ok": False, "message": f"Judge failed: {e}"}
                n.dismiss()
                ui.notify(res["message"], type="positive" if res["ok"] else "negative")
                evaluation.refresh()

            def table(title, rows):
                ui.label(title).classes("text-base font-medium mt-4")
                if not rows:
                    ui.label("Nothing to show yet.").classes("text-grey")
                    return
                # a row whose "metric" has an entry in INFO gets an (i) icon with a tooltip
                rows = [{**r, "_k": i, "_info": INFO.get(r.get("metric"), "")} for i, r in enumerate(rows)]
                cols = [{"name": k, "label": k.replace("_", " "), "field": k, "align": "left"} for k in rows[0] if not k.startswith("_")]
                t = ui.table(columns=cols, rows=rows, row_key="_k").classes("w-full").props("dense flat bordered")
                if any(r["_info"] for r in rows) and "metric" in rows[0]:
                    t.add_slot("body-cell-metric", """<q-td :props="props">{{ props.value }}
                        <q-icon v-if="props.row._info" name="info" size="xs" color="grey-7" class="q-ml-xs">
                        <q-tooltip max-width="340px" class="text-body2">{{ props.row._info }}</q-tooltip></q-icon></q-td>""")

            ev_state = {"all_fields": False}

            def note(text):
                ui.label(text).classes("text-caption text-grey")

            @ui.refreshable
            async def evaluation():
                try:
                    runs, ev, ext_rows, judged, ev_all = await _load(_eval_inputs)
                except DbDown:
                    return db_down_notice(evaluation.refresh)
                fields = {r["bill_id"]: db.extraction_fields(r) for r in ext_rows if r["source"] == "sample"}
                up_fields = {r["bill_id"]: db.extraction_fields(r) for r in ext_rows if r["source"] == "upload"}
                prices = {"extractor": (s.gemini_price_in, s.gemini_price_out), "judge": (s.mistral_price_in, s.mistral_price_out)}
                m = metrics.summarize(runs, ev, fields, prices)

                ui.label("Bills (scan on the Bills tab, then run the judge one at a time)").classes("text-base font-medium")
                rows = [{"bill_id": r["bill_id"], "noise": r["noise_level"] or "uploaded", "scanned": "yes",
                         "judged": "yes" if r["bill_id"] in judged else "no"} for r in ext_rows]
                if not rows:
                    ui.label("No scanned bills yet.").classes("text-grey")
                    return
                t = ui.table(columns=[{"name": c, "label": c, "field": c, "align": "left"} for c in ("bill_id", "noise", "scanned", "judged")] +
                             [{"name": "action", "label": "", "field": "action"}], rows=rows, row_key="bill_id").classes("w-full").props("dense flat bordered")
                t.add_slot("body-cell-action", """<q-td :props="props"><q-btn size="sm" dense outline
                    :label="props.row.judged === 'yes' ? 'Re-judge' : 'Judge'"
                    @click.stop="() => $parent.$emit('judge', props.row.bill_id)" /></q-td>""")
                t.on("judge", lambda e: do_judge(e.args))

                a = m["accuracy"]
                table("Extraction accuracy vs ground truth",
                      [{"metric": "Bills evaluated", "value": a["bills"]},
                       {"metric": "All fields match (%)", "value": a["all_fields_pct"]},
                       {"metric": "Critical fields match (%)", "value": a["critical_fields_pct"]},
                       {"metric": "Bill-level: all critical fields correct (%)", "value": a["bill_level_pct"]},
                       {"metric": "Arithmetic-consistent bills (%), of checkable", "value": a["arithmetic_consistent_pct"]},
                       {"metric": "Bills not arithmetic-checkable (missing values)", "value": a["arithmetic_not_checkable"]}])
                table("Per-bill results", m["per_bill"])
                table("Field mismatches: extractor vs ground truth (with judge verdict)", m["mismatches"])
                ranked = sorted(a["per_field"].items(), key=lambda kv: (kv[1] is None, kv[1] if kv[1] is not None else 0, KEYS.index(kv[0])))
                shown = ranked if ev_state["all_fields"] else ranked[:10]
                table("Per-field exact match: extractor vs ground truth (%), lowest first",
                      [{"field": LABELS[k], "match_pct": v} for k, v in shown])
                if len(ranked) > 10:
                    def toggle():
                        ev_state["all_fields"] = not ev_state["all_fields"]
                        evaluation.refresh()
                    ui.button(f"Show fewer fields" if ev_state["all_fields"] else f"Show all {len(ranked)} fields", on_click=toggle).props("flat dense no-caps size=sm")
                j = m["judge_quality"]
                table("LLM judge: coverage", [{"metric": "fields judged", "value": j.get("fields_judged")},
                                              {"metric": "bills judged", "value": j.get("bills_judged")},
                                              {"metric": "agreement with ground truth (%)", "value": j.get("agreement_pct")},
                                              {"metric": "fields without verdict", "value": j.get("fields_without_verdict")}])
                if j.get("matrix"):
                    mx = j["matrix"]
                    table("LLM judge vs ground truth: confusion matrix (fields)",
                          [{"ground truth": "Extractor wrong", **{c.replace("_", " "): v for c, v in mx["extractor_wrong"].items()}},
                           {"ground truth": "Extractor right", **{c.replace("_", " "): v for c, v in mx["extractor_right"].items()}}])
                    table("LLM judge: precision and recall",
                          [{"metric": "precision (%)", "value": j.get("precision_pct"), "meaning": "of fields the judge flagged, share that were really wrong"},
                           {"metric": "recall (%)", "value": j.get("recall_pct"), "meaning": "of really wrong fields, share the judge flagged (no verdict counts as missed)"}])
                table("Judge disagreements with ground truth", j.get("disagreements", []))
                if up_fields:
                    u = metrics.upload_summary([r for r in ev_all if r["source"] == "upload"], up_fields)
                    table("Uploaded bills (judge-estimated, no ground truth)",
                          [{"metric": "Bills scanned", "value": u["bills_scanned"]},
                           {"metric": "Bills judged", "value": u["bills_judged"]},
                           {"metric": "Bills with all critical fields read (%)", "value": u["critical_complete_pct"]},
                           {"metric": "Fields judged correct (%)", "value": u["fields_judged_correct_pct"]},
                           {"metric": "Bills with all critical fields judged correct (%)", "value": u["bills_critical_all_correct_pct"]},
                           {"metric": "fields without verdict", "value": u["fields_without_verdict"]}])
                table("Usage (tokens)", [{"role": r, **u} for r, u in m["usage"].items()])
                table("Latency (ms): provider response time of the final attempt, excluding pacing and retry waits",
                      [{"role": r, **v} for r, v in m["latency_ms"].items()])
                table("Reliability", [{"role": r, **v} for r, v in m["reliability"].items()])
                note("Noise level is assigned when each synthetic bill is generated (not detected from the image): "
                     "clean = digital render with light grain; medium = slight skew, blur and shading; "
                     "noisy = stronger skew, blur, shading, low contrast and heavy grain.")
                bn = m["by_noise"]
                pick = lambda keys: [{"noise": lv, **{k: v[k] for k in keys}} for lv, v in bn.items()]
                table("By noise level: extraction accuracy", pick(["bills", "all_fields_pct", "critical_fields_pct", "bill_level_pct", "arithmetic_consistent_pct"]))
                table("By noise level: extractor usage, latency and reliability",
                      pick(["extractor_calls", "extractor_avg_in_tokens", "extractor_avg_out_tokens", "extractor_latency_avg_ms",
                            "extractor_latency_median_ms", "extractor_latency_p90_ms", "extractor_error_rate_pct"]))
                table("By noise level: judge usage, latency, reliability and agreement",
                      pick(["judge_calls", "judge_avg_in_tokens", "judge_avg_out_tokens", "judge_latency_avg_ms",
                            "judge_latency_p90_ms", "judge_error_rate_pct", "judge_agreement_pct"]))

            lazy["Evaluation"] = (eval_box, evaluation, evaluation.refresh)

        # ---------------- Field guide tab (static, built on first open) ----------------
        with ui.tab_panel(t_guide):
            guide_box = ui.column().classes("w-full")

            def field_guide():
                ui.label(f"All {len(KEYS)} fields read from each bill. Critical fields drive the headline accuracy numbers.").classes("text-grey")
                rows = [{"field": LABELS[k], "group": "Critical" if k in PRIMARY else "Other", "meaning": MEANINGS[k]} for k in KEYS]
                cols = [{"name": c, "label": c.capitalize(), "field": c, "align": "left"} for c in ("field", "group", "meaning")]
                ui.table(columns=cols, rows=rows, row_key="field", pagination=0).classes("w-full").props("dense flat bordered")
            lazy["Field guide"] = (guide_box, field_guide, None)

    async def on_tab(value):
        name = value if isinstance(value, str) else value.props["name"]   # tab_panels reports the tab name
        if name not in lazy:
            return                                               # Bills is kept up to date by scans and filters
        box, build, refresh = lazy[name]
        if name in built:
            if refresh:
                refresh()                                        # reads are cached, so this is cheap
        else:
            built.add(name)
            with box:
                r = build()
                if inspect.isawaitable(r):
                    await r
