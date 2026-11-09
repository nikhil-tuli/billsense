"""Entry point: `python main.py` (Render sets PORT)."""
import os

from nicegui import app as nicegui_app, run, ui

import app.ui as _pages  # noqa: F401  (registers pages and routes)
from app import db, seed
from app.config import get_settings


async def startup():
    s = get_settings()
    if s.database_url and s.writes_enabled:
        await run.io_bound(db.init_schema)
        await run.io_bound(seed.seed_if_empty)
    if s.database_url:
        try:                                                   # pre-load reads so the first visit is fast
            await run.io_bound(db.warm)
        except Exception:                                      # noqa: BLE001 - never block startup on this
            pass


nicegui_app.on_startup(startup)
nicegui_app.on_shutdown(db.close_pool)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Electricity bill extraction", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
           reload=False, show=False)
