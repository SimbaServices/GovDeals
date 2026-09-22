from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from app.config import HOST, IMAGE_DIR, PORT, ROOT, SCRAPE_INTERVAL_HOURS
from app.db import get_db, init_db, rows_to_dicts
from app.hooks import assign_hooks_for_active, fire_due_hooks
from app.images import image_path, images_by_listing, images_for_listing
from app.scheduler import start_scheduler
from app.scraper import scrape_once

templates = Jinja2Templates(directory=str(ROOT / "templates"))


def _minutes_left(end_utc: str | None) -> int | None:
    if not end_utc:
        return None
    try:
        end = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = end - datetime.now(timezone.utc)
    return int(delta.total_seconds() // 60)


def _decorate(listing: dict) -> dict:
    listing["minutes_left"] = _minutes_left(listing.get("auction_end_utc"))
    listing["in_final_window"] = (
        listing.get("status") == "active"
        and listing.get("minutes_left") is not None
        and 0 <= listing["minutes_left"] <= 120
    )
    return listing


def create_app(enable_scheduler: bool = True) -> FastAPI:
    init_db()
    app = FastAPI(title="GovDeals Equipment Tracker")
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.on_event("startup")
    def _startup() -> None:
        if enable_scheduler:
            start_scheduler()

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, category: str = "all", status: str = "active"):
        where = ["1=1"]
        params: list = []
        if category in {"skid_steer", "forklift_5k"}:
            where.append("watch_category = ?")
            params.append(category)
        if status in {"active", "ended"}:
            where.append("status = ?")
            params.append(status)
        sql = f"""
            SELECT * FROM listings
            WHERE {' AND '.join(where)}
            ORDER BY
                CASE status WHEN 'active' THEN 0 ELSE 1 END,
                auction_end_utc ASC
        """
        with get_db() as conn:
            listings = [_decorate(row) for row in rows_to_dicts(conn.execute(sql, params).fetchall())]
            photos = images_by_listing(conn, [row["id"] for row in listings])
            for row in listings:
                row["images"] = photos.get(row["id"], [])
                row["thumb"] = row["images"][0]["url"] if row["images"] else None
            stats = {
                "skid_active": conn.execute(
                    "SELECT COUNT(*) FROM listings WHERE watch_category='skid_steer' AND status='active'"
                ).fetchone()[0],
                "fork_active": conn.execute(
                    "SELECT COUNT(*) FROM listings WHERE watch_category='forklift_5k' AND status='active'"
                ).fetchone()[0],
                "ending_soon": conn.execute(
                    """
                    SELECT COUNT(*) FROM listings
                    WHERE status='active' AND auction_end_utc IS NOT NULL
                    """
                ).fetchone()[0],
                "hooks_pending": conn.execute(
                    "SELECT COUNT(*) FROM auction_hooks WHERE status='pending'"
                ).fetchone()[0],
                "hooks_fired": conn.execute(
                    "SELECT COUNT(*) FROM auction_hooks WHERE status='fired'"
                ).fetchone()[0],
                "last_run": conn.execute(
                    "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1"
                ).fetchone(),
            }
            stats["ending_soon"] = sum(1 for row in listings if row.get("in_final_window"))
            stats["last_run"] = dict(stats["last_run"]) if stats["last_run"] else None
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "listings": listings,
                "stats": stats,
                "category": category,
                "status": status,
                "scrape_hours": SCRAPE_INTERVAL_HOURS,
            },
        )

    @app.get("/listing/{listing_id}", response_class=HTMLResponse)
    def listing_detail(request: Request, listing_id: int):
        with get_db() as conn:
            listing = conn.execute(
                "SELECT * FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()
            if listing is None:
                return HTMLResponse("Listing not found", status_code=404)
            snapshots = rows_to_dicts(
                conn.execute(
                    """
                    SELECT * FROM price_snapshots
                    WHERE listing_id = ?
                    ORDER BY scraped_at ASC
                    """,
                    (listing_id,),
                ).fetchall()
            )
            hooks = rows_to_dicts(
                conn.execute(
                    """
                    SELECT * FROM auction_hooks
                    WHERE listing_id = ?
                    ORDER BY minutes_before_end DESC
                    """,
                    (listing_id,),
                ).fetchall()
            )
            photos = images_for_listing(conn, listing_id)
        decorated = _decorate(dict(listing))
        decorated["images"] = photos
        return templates.TemplateResponse(
            "listing.html",
            {
                "request": request,
                "listing": decorated,
                "snapshots": snapshots,
                "hooks": hooks,
            },
        )

    @app.get("/hooks", response_class=HTMLResponse)
    def hooks_page(request: Request, hook_status: str = "all"):
        where = "1=1"
        params: list = []
        if hook_status in {"pending", "fired", "missed"}:
            where = "h.status = ?"
            params.append(hook_status)
        with get_db() as conn:
            hooks = rows_to_dicts(
                conn.execute(
                    f"""
                    SELECT h.*, l.title, l.url, l.watch_category, l.current_price, l.status AS listing_status
                    FROM auction_hooks h
                    JOIN listings l ON l.id = h.listing_id
                    WHERE {where}
                    ORDER BY h.scheduled_at ASC
                    """,
                    params,
                ).fetchall()
            )
        return templates.TemplateResponse(
            "hooks.html",
            {"request": request, "hooks": hooks, "hook_status": hook_status},
        )

    @app.post("/actions/scrape")
    def action_scrape():
        scrape_once()
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/hooks")
    def action_hooks():
        assign_hooks_for_active()
        fire_due_hooks()
        return RedirectResponse("/hooks", status_code=303)

    @app.get("/download/database")
    def download_database():
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        dest = sqlite3.connect(tmp.name)
        try:
            with get_db() as src:
                src.backup(dest)
        finally:
            dest.close()
        return FileResponse(
            tmp.name,
            filename="govdeals.db",
            media_type="application/vnd.sqlite3",
            background=BackgroundTask(os.unlink, tmp.name),
        )

    @app.get("/api/listings")
    def api_listings():
        with get_db() as conn:
            return rows_to_dicts(conn.execute("SELECT * FROM listings ORDER BY auction_end_utc").fetchall())

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/media/{listing_id}/{filename}")
    def listing_image(listing_id: int, filename: str):
        path = image_path(listing_id, filename)
        if path is None or not path.is_file():
            return HTMLResponse("Image not found", status_code=404)
        return FileResponse(path)

    return app


app = create_app()


def serve(enable_scheduler: bool = True) -> None:
    import uvicorn

    uvicorn.run(
        create_app(enable_scheduler=enable_scheduler),
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
