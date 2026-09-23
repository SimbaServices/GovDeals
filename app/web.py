from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from app.config import HOST, IMAGE_DIR, PORT, ROOT, SCRAPE_INTERVAL_HOURS
from app.db import get_db, init_db, rows_to_dicts
from app.hooks import assign_hooks_for_active, fire_due_hooks
from app.images import image_path, images_by_listing, images_for_listing
from app.prefix import (
    prefix_from_header,
    public_path,
    reset_current_prefix,
    set_current_prefix,
    with_public_prefix,
)
from app.scheduler import start_scheduler
from app.scraper import scrape_once

templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.globals["public_path"] = public_path
templates.env.filters["with_public_prefix"] = with_public_prefix


class ForwardedPrefixMiddleware:
    """Honor X-Forwarded-Prefix if a reverse proxy mounts the app under a path."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        prefix = ""
        for key, value in scope.get("headers") or []:
            if key.lower() == b"x-forwarded-prefix":
                prefix = prefix_from_header(value.decode("latin1"))
                break
        token = set_current_prefix(prefix)

        async def send_prefixed(message):
            if prefix and message["type"] == "http.response.start":
                headers = []
                for key, value in message.get("headers") or []:
                    if key.lower() == b"location":
                        location = value.decode("latin1")
                        if location.startswith("/") and not (
                            location == prefix or location.startswith(prefix + "/")
                        ):
                            value = f"{prefix}{location}".encode("latin1")
                    headers.append((key, value))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_prefixed)
        finally:
            reset_current_prefix(token)


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

    @app.exception_handler(404)
    async def page_not_found(request: Request, _exc: Exception):
        accept = (request.headers.get("accept") or "").lower()
        if "text/html" in accept and "application/json" not in accept:
            home = public_path("/")
            page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>404 Page not found</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
           font-family: Georgia, "Times New Roman", serif; background: #f4f1ea; color: #1c1915; }}
    main {{ text-align: center; padding: 2rem; }}
    h1 {{ font-size: 4rem; margin: 0; letter-spacing: 0.04em; }}
    p {{ margin: 0.75rem 0 0; font-size: 1.25rem; }}
    a {{ color: inherit; }}
  </style>
</head>
<body>
  <main>
    <h1>404</h1>
    <p>Page not found.</p>
    <p><a href="{home}">GovDeals Tracker</a></p>
  </main>
</body>
</html>
"""
            return HTMLResponse(page, status_code=404)
        return JSONResponse({"detail": "Not Found"}, status_code=404)

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
            photos = images_by_listing(conn, [row["listing_id"] for row in hooks])
            for row in hooks:
                row["images"] = photos.get(row["listing_id"], [])
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

    return ForwardedPrefixMiddleware(app)


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
