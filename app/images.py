from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import ASSET_CDN, IMAGE_DIR, IMAGE_PAUSE_SECONDS, SITE_ORIGIN, USER_AGENT
from app.db import utcnow

SAFE_NAME = re.compile(r"^\d{3}\.(jpg|jpeg|png|webp|gif)$", re.IGNORECASE)
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def resolve_photo_url(photo: Any) -> str | None:
    if not photo:
        return None
    if isinstance(photo, dict):
        photo = (
            photo.get("url")
            or photo.get("photoUrl")
            or photo.get("fileName")
            or photo.get("path")
        )
    value = str(photo).strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/photos/") or value.startswith("/assets/"):
        return f"{ASSET_CDN}{value}"
    if value.startswith("/"):
        return f"{ASSET_CDN}{value}"
    account = value.split("_", 1)[0]
    return f"{ASSET_CDN}/photos/{account}/{value}"


def collect_photo_urls(*groups: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if not group:
            continue
        items = group if isinstance(group, list) else [group]
        for item in items:
            resolved = resolve_photo_url(item)
            if not resolved:
                continue
            key = resolved.split("?", 1)[0]
            if key in seen:
                continue
            seen.add(key)
            urls.append(resolved)
    return urls


def listing_image_dir(listing_id: int) -> Path:
    path = IMAGE_DIR / str(listing_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def filename_for(sort_order: int, url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext not in ALLOWED_EXT:
        ext = ".jpg"
    return f"{sort_order:03d}{ext}"


def media_url(listing_id: int, filename: str) -> str:
    return f"/media/{listing_id}/{filename}"


def image_path(listing_id: int, filename: str) -> Path | None:
    if not SAFE_NAME.match(filename):
        return None
    path = (IMAGE_DIR / str(listing_id) / filename).resolve()
    root = IMAGE_DIR.resolve()
    if root not in path.parents:
        return None
    return path


def existing_source_urls(conn, listing_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT source_url FROM listing_images WHERE listing_id = ?",
        (listing_id,),
    ).fetchall()
    return {row["source_url"] for row in rows}


def listing_has_images(conn, listing_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM listing_images WHERE listing_id = ? LIMIT 1",
        (listing_id,),
    ).fetchone()
    return row is not None


def next_sort_order(conn, listing_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) AS max_order FROM listing_images WHERE listing_id = ?",
        (listing_id,),
    ).fetchone()
    return int(row["max_order"]) + 1


def sync_listing_images(client, conn, listing_id: int, urls: list[str]) -> int:
    saved = 0
    known = existing_source_urls(conn, listing_id)
    sort_order = next_sort_order(conn, listing_id)
    dest_dir = listing_image_dir(listing_id)
    for url in urls:
        if url in known:
            continue
        filename = filename_for(sort_order, url)
        dest = dest_dir / filename
        try:
            response = client.client.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": SITE_ORIGIN + "/",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            dest.write_bytes(response.content)
        except Exception:
            time.sleep(IMAGE_PAUSE_SECONDS)
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO listing_images (
                listing_id, source_url, filename, sort_order, downloaded_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (listing_id, url, filename, sort_order, utcnow()),
        )
        known.add(url)
        sort_order += 1
        saved += 1
        time.sleep(IMAGE_PAUSE_SECONDS)
    return saved


def images_for_listing(conn, listing_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT filename, sort_order
        FROM listing_images
        WHERE listing_id = ?
        ORDER BY sort_order ASC
        """,
        (listing_id,),
    ).fetchall()
    return [
        {
            "filename": row["filename"],
            "url": media_url(listing_id, row["filename"]),
            "sort_order": row["sort_order"],
        }
        for row in rows
    ]


def images_by_listing(conn, listing_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {listing_id: [] for listing_id in listing_ids}
    if not listing_ids:
        return grouped
    placeholders = ",".join("?" * len(listing_ids))
    rows = conn.execute(
        f"""
        SELECT listing_id, filename, sort_order
        FROM listing_images
        WHERE listing_id IN ({placeholders})
        ORDER BY listing_id, sort_order
        """,
        listing_ids,
    ).fetchall()
    for row in rows:
        grouped[row["listing_id"]].append(
            {
                "filename": row["filename"],
                "url": media_url(row["listing_id"], row["filename"]),
                "sort_order": row["sort_order"],
            }
        )
    return grouped
