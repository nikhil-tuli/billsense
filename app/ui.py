"""NiceGUI pages: Bills, Extracted data, Evaluation."""
from fastapi import Response
from nicegui import app as nicegui_app, run, ui

from app import cascade, db, metrics, services
from app.config import get_settings
from app.fields import LABELS, PRIMARY, SECONDARY

PRIMARY_COLS = [{"name": k, "label": LABELS[k], "field": k, "align": "left"} for k in PRIMARY]


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
    try:
        return await run.io_bound(fn, *a)
    except Exception as e:                                   # noqa: BLE001 - Neon waking up, network, etc.
        raise DbDown(str(e)) from e


def db_down_notice(refresh):
    ui.label("The database is waking up or unavailable. Wait a few seconds, then retry.").classes("text-negative")
    ui.button("Retry", on_click=refresh).props("outline size=sm")


@ui.page("/")
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
        props = await _load(db.properties)
    except DbDown:
        db_down_notice(lambda: ui.navigate.reload())
        return
    state = {"city": None, "owner": None, "property": None, "bill": None}

    with ui.tabs().classes("w-full") as tabs:
        t_bills = ui.tab("Bills")
        t_data = ui.tab("Extracted data")
        t_eval = ui.tab("Evaluation")

    # ---------------- Bills tab ----------------
    @ui.refreshable
    async def bills_table():
        try:
            all_bills = await _load(db.bills)
        except DbDown:
            return db_down_notice(bills_table.refresh)
        rows = []
        for b in cascade.filter_bills(all_bills, state):
            rows.append({"bill_id": b["bill_id"], "property": b["property_name"], "city": b["city"],
                         "owner": b["owner"], "period": f'{b["bill_period_start"]:%d/%m/%Y} - {b["bill_period_end"]:%d/%m/%Y}',
                         "status": "Scanned" if b["scan_status"] == "scanned" else "Not scanned",
                         "scanned": b["scan_status"] == "scanned"})
        cols = [{"name": "property", "label": "Property", "field": "property", "align": "left"},
                {"name": "city", "label": "City", "field": "city", "align": "left"},
                {"name": "owner", "label": "Owner", "field": "owner", "align": "left"},
                {"name": "period", "label": "Bill period", "field": "period", "align": "left"},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
                {"name": "action", "label": "", "field": "action"}]
        t = ui.table(columns=cols, rows=rows, row_key="bill_id").classes("w-full")
        t.add_slot("body-cell-action", """<q-td :props="props">
            <q-btn size="sm" dense outline :label="props.row.scanned ? 'Re-scan' : 'Scan'"
                   @click.stop="() => $parent.$emit('scan', props.row.bill_id)" /></q-td>""")
        t.on("scan", lambda e: do_scan(e.args))
        t.on("rowClick", lambda e: select_bill(e.args[1]["bill_id"]))
        if not rows:
            ui.label("No bills match the selected filters.").classes("text-grey")

    @ui.refreshable
    async def bill_detail():
        bid = state["bill"]
        if not bid:
            ui.label("Select a bill to see its image and extracted fields.").classes("text-grey")
            return
        try:
            row = await _load(db.get_extraction, bid)
        except DbDown:
            return db_down_notice(bill_detail.refresh)
        fields = db.extraction_fields(row)
        with ui.row().classes("w-full no-wrap gap-6"):
            ui.image(f"/bill_image/{bid}").props("fit=contain").classes("w-96")
            with ui.column().classes("gap-1"):
                if not row:
                    ui.label("Not scanned yet. Press Scan on the bill row.").classes("text-grey")
                else:
                    for k, v in _kv(fields, PRIMARY):
                        with ui.row().classes("gap-2"):
                            ui.label(k + ":").classes("text-grey w-44")
                            ui.label(v).classes("font-medium")
                    with ui.expansion("Secondary fields").classes("w-full"):
                        for k, v in _kv(fields, SECONDARY):
                            with ui.row().classes("gap-2"):
                                ui.label(k + ":").classes("text-grey w-44")
                                ui.label(v)

    async def select_bill(bid):
        state["bill"] = bid
        bill_detail.refresh()

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
        state["bill"] = bid
        bills_table.refresh()
        bill_detail.refresh()

    def on_filter(name):
        def handler(e):
            state[name] = e.value
            state.update(cascade.normalise(props, {k: state[k] for k in ("city", "owner", "property")}, keep=name))
            opts = cascade.options(props, state)
            for n, sel in selects.items():
                sel.options = opts[n]
                sel.value = state[n]
                sel.update()
            bills_table.refresh()
        return handler

    selects = {}
    with ui.tab_panels(tabs, value=t_bills, on_change=lambda e: on_tab(e.value)).classes("w-full") as panels:
        with ui.tab_panel(t_bills):
            opts = cascade.options(props, state)
            with ui.row().classes("w-full gap-4"):
                for name, label in (("city", "City"), ("owner", "Owner"), ("property", "Property name")):
                    selects[name] = ui.select(opts[name], label=label, value=None, clearable=True,
                                              on_change=on_filter(name)).classes("w-64")
            await bills_table()
            ui.separator()
            await bill_detail()

        # ---------------- Extracted data tab ----------------
        with ui.tab_panel(t_data):
            @ui.refreshable
            async def data_table():
                try:
                    rows_db = await _load(db.extracted_rows)
                except DbDown:
                    return db_down_notice(data_table.refresh)
                rows = []
                for r in rows_db:
                    f = db.extraction_fields(r)
                    row = {"bill_id": r["bill_id"], "city": r["city"], "owner": r["owner"],
                           "property_name": r["property_name"]}
                    row.update({k: _fmt(f.get(k)) for k in PRIMARY})
                    row["secondary"] = _kv(f, SECONDARY)
                    rows.append(row)
                cols = ([{"name": "city", "label": "City", "field": "city", "align": "left"},
                         {"name": "owner", "label": "Owner", "field": "owner", "align": "left"},
                         {"name": "property_name", "label": "Property name", "field": "property_name", "align": "left"}]
                        + PRIMARY_COLS)
                if not rows:
                    ui.label("No bills have been scanned yet.").classes("text-grey")
                    return
                t = ui.table(columns=cols, rows=rows, row_key="bill_id").classes("w-full")
                t.add_slot("header", """<q-tr :props="props"><q-th auto-width />
                    <q-th v-for="col in props.cols" :key="col.name" :props="props">{{ col.label }}</q-th></q-tr>""")
                t.add_slot("body", """<q-tr :props="props"><q-td auto-width>
                      <q-btn size="sm" flat round dense @click="props.expand = !props.expand"
                             :icon="props.expand ? 'remove' : 'add'" /></q-td>
                    <q-td v-for="col in props.cols" :key="col.name" :props="props">{{ col.value }}</q-td></q-tr>
                    <q-tr v-if="props.expand" :props="props"><q-td colspan="100%">
                      <div class="row q-gutter-md no-wrap">
                        <img :src="'/bill_image/' + props.row.bill_id" style="max-width:320px" />
                        <div><div v-for="kv in props.row.secondary" :key="kv[0]">{{ kv[0] }}: <b>{{ kv[1] }}</b></div></div>
                      </div></q-td></q-tr>""")
            await data_table()

        # ---------------- Evaluation tab ----------------
        with ui.tab_panel(t_eval):
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
                rows = [{**r, "_k": i} for i, r in enumerate(rows)]          # unique row key for Quasar
                cols = [{"name": k, "label": k.replace("_", " "), "field": k, "align": "left"} for k in rows[0] if k != "_k"]
                ui.table(columns=cols, rows=rows, row_key="_k").classes("w-full").props("dense flat bordered")

            @ui.refreshable
            async def evaluation():
                try:
                    runs, ev, ext_rows, judged = await _load(lambda: (db.all_runs(), db.all_eval(), db.extracted_rows(), db.judged_bills()))
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
                table("Per-field exact match (%)", [{"field": LABELS[k], "match_pct": v} for k, v in a["per_field"].items()])
                j = m["judge_quality"]
                table("LLM judge vs ground truth", [{"metric": k.replace("_", " "), "value": v} for k, v in j.items() if k != "disagreements"])
                table("Judge disagreements with ground truth", j.get("disagreements", []))
                table("Usage (tokens)", [{"role": r, **u} for r, u in m["usage"].items()])
                table("Latency (ms): provider response time of the final attempt, excluding pacing and retry waits",
                      [{"role": r, **v} for r, v in m["latency_ms"].items()])
                table("Reliability", [{"role": r, **v} for r, v in m["reliability"].items()])
                bn = m["by_noise"]
                pick = lambda keys: [{"noise": lv, **{k: v[k] for k in keys}} for lv, v in bn.items()]
                table("By noise level: extraction accuracy", pick(["bills", "all_fields_pct", "critical_fields_pct", "bill_level_pct", "arithmetic_consistent_pct"]))
                table("By noise level: extractor usage, latency and reliability",
                      pick(["extractor_calls", "extractor_avg_in_tokens", "extractor_avg_out_tokens", "extractor_latency_avg_ms",
                            "extractor_latency_median_ms", "extractor_latency_p90_ms", "extractor_error_rate_pct"]))
                table("By noise level: judge usage, latency, reliability and agreement",
                      pick(["judge_calls", "judge_avg_in_tokens", "judge_avg_out_tokens", "judge_latency_avg_ms",
                            "judge_latency_p90_ms", "judge_error_rate_pct", "judge_agreement_pct"]))

            await evaluation()

    async def on_tab(value):
        name = value if isinstance(value, str) else value.props["name"]   # tab_panels reports the tab name
        if name == "Extracted data":
            data_table.refresh()
        elif name == "Evaluation":
            evaluation.refresh()
        elif name == "Bills":
            bills_table.refresh()
            bill_detail.refresh()
