from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from html import unescape
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.config import (
    BUSINESS_ID,
    DETAIL_PAUSE_SECONDS,
    MAESTRO_API_KEY,
    MAESTRO_URL,
    OCP_APIM_KEY,
    PAGE_SIZE,
    REQUEST_PAUSE_SECONDS,
    SEARCHES,
    SITE_ID,
    SITE_ORIGIN,
    USER_AGENT,
)
from app.db import get_db, utcnow
from app.hooks import assign_hooks_for_listing

CAPACITY_LB_RE = re.compile(
    r"(?P<n>\d{1,2}(?:[, ]\d{3})|\d{4,5})\s*(?:lb|lbs|pound|#)\b",
    re.IGNORECASE,
)
FIVEK_RE = re.compile(
    r"\b5[\s,-]?k(?:lb|lbs|#)?\b|\b5[, ]?000\b",
    re.IGNORECASE,
)
FORKLIFT_RE = re.compile(r"forklift|lift truck|towmotor", re.IGNORECASE)
ATTACHMENT_RE = re.compile(
    r"root rake|log grapple|v-plow|skid tracks\b|bucket only|forks only|"
    r"\battachment\b|smooth edge|mini excavator",
    re.IGNORECASE,
)


def _headers(referer: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": SITE_ORIGIN,
        "Referer": referer,
        "User-Agent": USER_AGENT,
        "x-api-key": MAESTRO_API_KEY,
        "x-user-id": "-1",
        "x-api-correlation-id": str(uuid.uuid4()),
        "x-ecom-session-id": str(uuid.uuid4()),
        "x-page-unique-id": str(uuid.uuid4()),
        "x-user-timezone": "America/Chicago",
        "x-referer": referer,
        "Ocp-Apim-Subscription-Key": OCP_APIM_KEY,
    }


def _facet_filter(category_id: str) -> str:
    return (
        '{!tag=product_category_external_id}'
        f'product_category_external_id:"{category_id}"'
    )


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(unescape(value), "html.parser")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def parse_time_remaining(value: str | None) -> int | None:
    if not value:
        return None
    parts = [p for p in str(value).split(":") if p != ""]
    if len(parts) != 4:
        return None
    try:
        days, hours, minutes, seconds = (int(float(p)) for p in parts)
    except ValueError:
        return None
    return days * 24 * 60 + hours * 60 + minutes + (1 if seconds else 0)


def is_fivek_forklift(title: str, description: str = "") -> bool:
    text = f"{title} {description}"
    if FIVEK_RE.search(text):
        return True
    for match in CAPACITY_LB_RE.finditer(text):
        pounds = int(re.sub(r"[^\d]", "", match.group("n")))
        if 4500 <= pounds <= 5500:
            return True
    return False


def looks_like_machine(title: str, watch_category: str) -> bool:
    if ATTACHMENT_RE.search(title):
        return False
    if watch_category == "forklift_5k":
        return bool(FORKLIFT_RE.search(title))
    return True


def listing_url(asset_id: int, account_id: int) -> str:
    return f"{SITE_ORIGIN}/en/asset/{asset_id}/{account_id}"


def photo_url(photo: str | None) -> str | None:
    if not photo:
        return None
    if photo.startswith("http"):
        return photo
    return f"https://webassets.lqdt1.com/assets/photos/{photo.split('_')[0]}/{photo}"


class GovDealsClient:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=30.0, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def search_category(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 1
        referer = f"{SITE_ORIGIN}{spec['referer_path']}"
        while True:
            payload = {
                "categoryIds": "",
                "businessId": BUSINESS_ID,
                "searchText": "*",
                "isQAL": False,
                "locationId": None,
                "model": "",
                "makebrand": "",
                "auctionTypeId": None,
                "page": page,
                "displayRows": PAGE_SIZE,
                "sortField": "auctionclose",
                "sortOrder": "asc",
                "requestType": "search",
                "responseStyle": "fullResponse",
                "facets": [
                    "categoryName",
                    "auctionTypeID",
                    "condition",
                    "saleEventName",
                    "sellerDisplayName",
                    "product_pricecents",
                    "isReserveMet",
                    "hasBuyNowPrice",
                    "isReserveNotMet",
                    "sellerType",
                    "warehouseId",
                    "region",
                    "currencyTypeCode",
                    "tierId",
                ],
                "facetsFilter": [_facet_filter(cid) for cid in spec["category_ids"]],
                "timeType": "",
                "sellerTypeId": None,
                "accountIds": [],
            }
            response = self.client.post(
                f"{MAESTRO_URL}/search/list",
                headers=_headers(referer),
                json=payload,
            )
            response.raise_for_status()
            batch = response.json().get("assetSearchResults") or []
            results.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1
            time.sleep(REQUEST_PAUSE_SECONDS)
        return results

    def search_text(self, query: str, referer: str) -> list[dict[str, Any]]:
        payload = {
            "businessId": BUSINESS_ID,
            "searchText": query,
            "isQAL": False,
            "page": 1,
            "displayRows": 40,
            "sortField": "auctionclose",
            "sortOrder": "asc",
            "requestType": "search",
            "responseStyle": "fullResponse",
            "facets": [],
            "facetsFilter": [],
        }
        response = self.client.post(
            f"{MAESTRO_URL}/search/list",
            headers=_headers(referer),
            json=payload,
        )
        response.raise_for_status()
        return response.json().get("assetSearchResults") or []

    def fetch_asset(self, asset_id: int, account_id: int) -> dict[str, Any] | None:
        referer = listing_url(asset_id, account_id)
        response = self.client.post(
            f"{MAESTRO_URL}/assets/{asset_id}/{account_id}/false",
            headers=_headers(referer),
            json={"businessId": BUSINESS_ID, "siteId": SITE_ID},
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()


def normalize_search_item(item: dict[str, Any], watch_category: str) -> dict[str, Any]:
    asset_id = int(item["assetId"])
    account_id = int(item["accountId"])
    title = item.get("assetShortDescription") or "Untitled listing"
    return {
        "asset_id": asset_id,
        "account_id": account_id,
        "auction_id": item.get("auctionId"),
        "watch_category": watch_category,
        "title": title,
        "description": html_to_text(item.get("assetLongDescription")),
        "url": listing_url(asset_id, account_id),
        "make": item.get("makebrand"),
        "model": item.get("model"),
        "year": item.get("modelYear"),
        "location_city": item.get("locationCity"),
        "location_state": item.get("locationState"),
        "seller": item.get("companyName"),
        "current_price": item.get("currentBid"),
        "bid_increment": item.get("assetBidIncrement"),
        "bid_count": item.get("bidCount"),
        "has_reserve": 1 if item.get("hasReservePrice") else 0,
        "reserve_not_met": 1 if item.get("isReserveNotMet") else 0,
        "time_remaining": item.get("timeRemaining"),
        "auction_start": item.get("assetAuctionStartDate"),
        "auction_end": item.get("assetAuctionEndDate"),
        "auction_end_utc": item.get("assetAuctionEndDateUtc"),
        "status": "ended" if item.get("isSoldAuction") else "active",
        "photo_url": photo_url(item.get("photo")),
        "minutes_remaining": parse_time_remaining(item.get("timeRemaining")),
    }


def upsert_listing(
    conn,
    record: dict[str, Any],
    source: str,
    hook_id: int | None = None,
) -> tuple[int, bool]:
    now = utcnow()
    existing = conn.execute(
        """
        SELECT id, description, status, current_price, hooks_assigned
        FROM listings
        WHERE asset_id = ? AND account_id = ? AND ifnull(auction_id, -1) = ifnull(?, -1)
        """,
        (record["asset_id"], record["account_id"], record.get("auction_id")),
    ).fetchone()

    if existing:
        listing_id = existing["id"]
        description = record.get("description") or existing["description"]
        status = record["status"]
        final_price = record.get("current_price") if status == "ended" else None
        conn.execute(
            """
            UPDATE listings SET
                title = ?, description = ?, url = ?, make = ?, model = ?, year = ?,
                location_city = ?, location_state = ?, seller = ?, current_price = ?,
                bid_increment = ?, bid_count = ?, has_reserve = ?, reserve_not_met = ?,
                time_remaining = ?, auction_start = ?, auction_end = ?,
                auction_end_utc = ?, status = ?,
                final_price = COALESCE(?, final_price),
                photo_url = COALESCE(?, photo_url),
                last_seen = ?
            WHERE id = ?
            """,
            (
                record["title"],
                description,
                record["url"],
                record.get("make"),
                record.get("model"),
                record.get("year"),
                record.get("location_city"),
                record.get("location_state"),
                record.get("seller"),
                record.get("current_price"),
                record.get("bid_increment"),
                record.get("bid_count"),
                record.get("has_reserve"),
                record.get("reserve_not_met"),
                record.get("time_remaining"),
                record.get("auction_start"),
                record.get("auction_end"),
                record.get("auction_end_utc"),
                status,
                final_price,
                record.get("photo_url"),
                now,
                listing_id,
            ),
        )
        created = False
    else:
        cursor = conn.execute(
            """
            INSERT INTO listings (
                asset_id, account_id, auction_id, watch_category, title, description,
                url, make, model, year, location_city, location_state, seller,
                current_price, bid_increment, bid_count, has_reserve, reserve_not_met,
                time_remaining, auction_start, auction_end, auction_end_utc, status,
                photo_url, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["asset_id"],
                record["account_id"],
                record.get("auction_id"),
                record["watch_category"],
                record["title"],
                record.get("description"),
                record["url"],
                record.get("make"),
                record.get("model"),
                record.get("year"),
                record.get("location_city"),
                record.get("location_state"),
                record.get("seller"),
                record.get("current_price"),
                record.get("bid_increment"),
                record.get("bid_count"),
                record.get("has_reserve"),
                record.get("reserve_not_met"),
                record.get("time_remaining"),
                record.get("auction_start"),
                record.get("auction_end"),
                record.get("auction_end_utc"),
                record["status"],
                record.get("photo_url"),
                now,
                now,
            ),
        )
        listing_id = cursor.lastrowid
        created = True

    conn.execute(
        """
        INSERT INTO price_snapshots (
            listing_id, price, time_remaining, minutes_remaining, source, hook_id, scraped_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            listing_id,
            record.get("current_price"),
            record.get("time_remaining"),
            record.get("minutes_remaining"),
            source,
            hook_id,
            now,
        ),
    )
    return listing_id, created


def refresh_description(client: GovDealsClient, record: dict[str, Any]) -> dict[str, Any]:
    detail = client.fetch_asset(record["asset_id"], record["account_id"])
    time.sleep(DETAIL_PAUSE_SECONDS)
    if not detail:
        return record
    record["description"] = html_to_text(detail.get("assetLongDesc")) or record.get(
        "description"
    )
    record["make"] = detail.get("makebrand") or record.get("make")
    record["model"] = detail.get("model") or record.get("model")
    record["year"] = detail.get("modelYear") or record.get("year")
    record["seller"] = detail.get("companyName") or record.get("seller")
    record["location_city"] = detail.get("city") or record.get("location_city")
    record["location_state"] = detail.get("state") or record.get("location_state")
    if not record.get("auction_end_utc") and detail.get("assetAuctionEndDate"):
        record["auction_end"] = detail.get("assetAuctionEndDate")
    return record


def ingest_items(
    conn,
    client: GovDealsClient,
    spec: dict[str, Any],
    items: list[dict[str, Any]],
    seen_keys: set[tuple[int, int, Any]],
) -> tuple[int, int]:
    upserted = 0
    hooks_assigned = 0
    for item in items:
        record = normalize_search_item(item, spec["watch_category"])
        key = (record["asset_id"], record["account_id"], record.get("auction_id"))
        if key in seen_keys:
            continue
        if not looks_like_machine(record["title"], spec["watch_category"]):
            continue
        if spec["require_fivek"] and not is_fivek_forklift(record["title"]):
            continue

        existing = conn.execute(
            """
            SELECT id, description FROM listings
            WHERE asset_id = ? AND account_id = ? AND ifnull(auction_id, -1) = ifnull(?, -1)
            """,
            (record["asset_id"], record["account_id"], record.get("auction_id")),
        ).fetchone()
        if existing is None or not existing["description"]:
            record = refresh_description(client, record)
            if spec["require_fivek"] and not is_fivek_forklift(
                record["title"], record.get("description") or ""
            ):
                continue

        seen_keys.add(key)
        listing_id, _created = upsert_listing(conn, record, source="scan")
        upserted += 1
        hooks_assigned += assign_hooks_for_listing(conn, listing_id)
    return upserted, hooks_assigned


def scrape_once() -> dict[str, Any]:
    started = utcnow()
    client = GovDealsClient()
    found = 0
    upserted = 0
    hooks_assigned = 0
    error = None
    seen_keys: set[tuple[int, int, Any]] = set()

    try:
        with get_db() as conn:
            for spec in SEARCHES:
                items = client.search_category(spec)
                time.sleep(REQUEST_PAUSE_SECONDS)
                added, hooked = ingest_items(conn, client, spec, items, seen_keys)
                upserted += added
                hooks_assigned += hooked

            keyword_spec = {"watch_category": "forklift_5k", "require_fivek": True}
            for query in ("5000 lb forklift", "5,000 lb forklift", "5k forklift"):
                extras = client.search_text(query, f"{SITE_ORIGIN}/en/forklifts")
                time.sleep(REQUEST_PAUSE_SECONDS)
                added, hooked = ingest_items(conn, client, keyword_spec, extras, seen_keys)
                upserted += added
                hooks_assigned += hooked

            found = len(seen_keys)
            mark_missing_ended(conn, seen_keys)
            conn.execute(
                """
                INSERT INTO scrape_runs (
                    started_at, finished_at, listings_found, listings_upserted, hooks_assigned
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (started, utcnow(), found, upserted, hooks_assigned),
            )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO scrape_runs (
                    started_at, finished_at, listings_found, listings_upserted, hooks_assigned, error
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (started, utcnow(), found, upserted, hooks_assigned, error),
            )
        raise
    finally:
        client.close()

    return {
        "started_at": started,
        "finished_at": utcnow(),
        "listings_found": found,
        "listings_upserted": upserted,
        "hooks_assigned": hooks_assigned,
        "error": error,
    }


def mark_missing_ended(conn, seen_keys: set[tuple[int, int, Any]]) -> None:
    active = conn.execute(
        "SELECT id, asset_id, account_id, auction_id, current_price, auction_end_utc FROM listings WHERE status = 'active'"
    ).fetchall()
    now = datetime.now(timezone.utc)
    for row in active:
        key = (row["asset_id"], row["account_id"], row["auction_id"])
        ended = False
        if key not in seen_keys and row["auction_end_utc"]:
            try:
                end = datetime.fromisoformat(row["auction_end_utc"].replace("Z", "+00:00"))
                if end <= now:
                    ended = True
            except ValueError:
                ended = False
        if ended:
            conn.execute(
                """
                UPDATE listings
                SET status = 'ended',
                    final_price = COALESCE(final_price, current_price),
                    last_seen = ?
                WHERE id = ?
                """,
                (utcnow(), row["id"]),
            )


def fetch_listing_live(client: GovDealsClient, listing: dict[str, Any]) -> dict[str, Any] | None:
    """Re-query one lot so a hook can capture the live bid."""
    watch = listing["watch_category"]
    query = listing.get("title") or f"{listing['asset_id']}"
    referer = listing.get("url") or f"{SITE_ORIGIN}/en/surplus-inventory"
    for item in client.search_text(query, referer):
        if int(item.get("assetId") or 0) == listing["asset_id"] and int(
            item.get("accountId") or 0
        ) == listing["account_id"]:
            return normalize_search_item(item, watch)
    time.sleep(REQUEST_PAUSE_SECONDS)
    specs = [spec for spec in SEARCHES if spec["watch_category"] == watch]
    for spec in specs:
        for item in client.search_category(spec):
            if int(item.get("assetId") or 0) == listing["asset_id"] and int(
                item.get("accountId") or 0
            ) == listing["account_id"]:
                return normalize_search_item(item, watch)
        time.sleep(REQUEST_PAUSE_SECONDS)
    return None
