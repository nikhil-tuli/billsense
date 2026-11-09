"""NiceGUI pages: Bills, Extracted data, Evaluation, Field guide."""
import logging
import time

from fastapi import Response
from nicegui import app as nicegui_app, run, ui

from app import cascade, db, export, metrics, services
from app.config import get_settings
from app.fields import KEYS, LABELS, MEANINGS, PRIMARY

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
    out = []
    for b in db.bills():
        scanned = b["scan_status"] == "scanned"
        out.append({**b, "scanned": scanned, "scan_label": "Scanned" if scanned else "Not scanned",
                    "reading_period": cascade.month_year(b["extracted_period_end"]) if scanned else ""})
    return out


def _eval_inputs():
    return db.all_runs(), db.all_eval(), db.extracted_rows(), db.judged_bills()


def _bills_and_eval():
    return _bill_rows(), db.all_eval()


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
                        "m": bool(r["extractor_match"]),
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
                         "status": b["scan_label"], "scanned": scanned, "noise": b["noise_level"],
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
                       :label="col.value" /></span>
                <span v-else>{{ col.value }}</span>
              </q-td>
            </q-tr>
            <q-tr v-if="props.row._open" :props="props">
              <q-td colspan="100%" class="bg-grey-1">
                <div class="row no-wrap q-gutter-md q-pa-sm">
                  <div style="width:260px;flex:none">
                    <img :src="'/bill_image/' + props.row.bill_id" style="width:100%;cursor:zoom-in;border:1px solid #ccc"
                         @click="props.row._zoom = true" />
                    <div class="text-caption text-grey q-mt-xs">{{ props.row.bill_id }} · noise: {{ props.row.noise }} · click image to enlarge</div>
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
                          <tr v-for="d in props.row.detail.filter(d => (props.row._all || d.p) && (!props.row._mm || !d.m))"
                              :key="d.k" :class="d.m ? '' : 'bg-red-1'">
                            <td style="padding:2px 6px;height:auto;white-space:normal;overflow-wrap:anywhere">{{ d.f }}</td><td style="padding:2px 6px;height:auto;white-space:normal;overflow-wrap:anywhere">{{ d.e }}</td><td style="padding:2px 6px;height:auto;white-space:normal;overflow-wrap:anywhere">{{ d.t }}</td>
                            <td style="padding:2px 6px;height:auto"><q-icon v-if="d.m" name="check" color="positive" /><q-icon v-else name="close" color="negative" /></td>
                            <td style="padding:2px 6px;height:auto">{{ d.j }}</td>
                          </tr>
                        </tbody>
                      </table>
                      <div class="q-mt-sm text-caption text-grey">
                        Showing {{ props.row.detail.filter(d => (props.row._all || d.p) && (!props.row._mm || !d.m)).length }}
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
            lazy["Extracted data"] = (data_box, data_table)

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
                    runs, ev, ext_rows, judged = await _load(_eval_inputs)
                except DbDown:
                    return db_down_notice(evaluation.refresh)
                fields = {r["bill_id"]: db.extraction_fields(r) for r in ext_rows}
                prices = {"extractor": (s.gemini_price_in, s.gemini_price_out), "judge": (s.mistral_price_in, s.mistral_price_out)}
                m = metrics.summarize(runs, ev, fields, prices)

                ui.label("Bills (scan on the Bills tab, then run the judge one at a time)").classes("text-base font-medium")
                rows = [{"bill_id": r["bill_id"], "noise": r["noise_level"], "scanned": "yes",
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

            lazy["Evaluation"] = (eval_box, evaluation)

        # ---------------- Field guide tab (static, built on first open) ----------------
        with ui.tab_panel(t_guide):
            guide_box = ui.column().classes("w-full")

            def field_guide():
                ui.label(f"All {len(KEYS)} fields read from each bill. Critical fields drive the headline accuracy numbers.").classes("text-grey")
                rows = [{"field": LABELS[k], "group": "Critical" if k in PRIMARY else "Other", "meaning": MEANINGS[k]} for k in KEYS]
                cols = [{"name": c, "label": c.capitalize(), "field": c, "align": "left"} for c in ("field", "group", "meaning")]
                ui.table(columns=cols, rows=rows, row_key="field", pagination=0).classes("w-full").props("dense flat bordered")
            lazy["Field guide"] = (guide_box, field_guide)

    async def on_tab(value):
        name = value if isinstance(value, str) else value.props["name"]   # tab_panels reports the tab name
        if name not in lazy:
            return                                               # Bills is kept up to date by scans and filters
        box, fn = lazy[name]
        if name in built:
            if hasattr(fn, "refresh"):
                fn.refresh()                                     # reads are cached, so this is cheap
        else:
            built.add(name)
            with box:
                await fn() if name != "Field guide" else fn()
