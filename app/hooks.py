from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.request import Request, urlopen

from app.config import HOOK_FIRE_GRACE_MINUTES, HOOK_OFFSETS_MINUTES, WEBHOOK_URL
from app.db import get_db, utcnow


def parse_end(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def hook_name(minutes_before_end: int) -> str:
    if minutes_before_end == 0:
        return "final_close"
    hours, minutes = divmod(minutes_before_end, 60)
    if minutes:
        return f"t_minus_{hours}h{minutes:02d}m"
    return f"t_minus_{hours}h"


def assign_hooks_for_listing(conn, listing_id: int) -> int:
    row = conn.execute(
        """
        SELECT id, auction_end_utc, status, hooks_assigned, title
        FROM listings WHERE id = ?
        """,
        (listing_id,),
    ).fetchone()
    if row is None or row["status"] == "ended":
        return 0

    end = parse_end(row["auction_end_utc"])
    if end is None:
        return 0

    now = datetime.now(timezone.utc)
    window_start = end - timedelta(minutes=max(HOOK_OFFSETS_MINUTES))
    if now > end + timedelta(minutes=HOOK_FIRE_GRACE_MINUTES):
        return 0

    created = 0
    for minutes in HOOK_OFFSETS_MINUTES:
        scheduled = end - timedelta(minutes=minutes)
        existing = conn.execute(
            """
            SELECT id, status FROM auction_hooks
            WHERE listing_id = ? AND minutes_before_end = ?
            """,
            (listing_id, minutes),
        ).fetchone()
        if existing:
            continue

        if scheduled < now - timedelta(minutes=HOOK_FIRE_GRACE_MINUTES):
            status = "missed"
        else:
            status = "pending"

        conn.execute(
            """
            INSERT INTO auction_hooks (
                listing_id, minutes_before_end, hook_name, scheduled_at, status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                listing_id,
                minutes,
                hook_name(minutes),
                scheduled.replace(microsecond=0).isoformat(),
                status,
            ),
        )
        created += 1

    if created and now >= window_start:
        conn.execute(
            "UPDATE listings SET hooks_assigned = 1 WHERE id = ?",
            (listing_id,),
        )
    elif created:
        conn.execute(
            "UPDATE listings SET hooks_assigned = 1 WHERE id = ?",
            (listing_id,),
        )
    return created


def assign_hooks_for_active() -> int:
    assigned = 0
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM listings WHERE status = 'active'"
        ).fetchall()
        for row in rows:
            assigned += assign_hooks_for_listing(conn, row["id"])
    return assigned


def _notify_webhook(payload: dict[str, Any]) -> None:
    if not WEBHOOK_URL:
        return
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10):  # noqa: S310
            pass
    except Exception:  # noqa: BLE001
        return


def fire_due_hooks() -> list[dict[str, Any]]:
    from app.scraper import GovDealsClient, fetch_listing_live, upsert_listing

    now = datetime.now(timezone.utc)
    fired: list[dict[str, Any]] = []
    client = GovDealsClient()
    try:
        with get_db() as conn:
            due = conn.execute(
                """
                SELECT h.*, l.asset_id, l.account_id, l.auction_id, l.watch_category,
                       l.title, l.url, l.current_price AS last_price
                FROM auction_hooks h
                JOIN listings l ON l.id = h.listing_id
                WHERE h.status = 'pending' AND h.scheduled_at <= ?
                ORDER BY h.scheduled_at ASC
                """,
                (now.replace(microsecond=0).isoformat(),),
            ).fetchall()

            for hook in due:
                listing = dict(hook)
                listing_id = hook["listing_id"]
                try:
                    live = fetch_listing_live(client, listing)
                    if live:
                        upsert_listing(
                            conn, live, source="hook", hook_id=hook["id"]
                        )
                        price = live.get("current_price")
                        remaining = live.get("time_remaining")
                        if hook["minutes_before_end"] == 0 or live.get("status") == "ended":
                            conn.execute(
                                """
                                UPDATE listings
                                SET status = 'ended',
                                    final_price = COALESCE(?, current_price),
                                    last_seen = ?
                                WHERE id = ?
                                """,
                                (price, utcnow(), listing_id),
                            )
                    else:
                        price = hook["last_price"]
                        remaining = "0:0:0:0"
                        if hook["minutes_before_end"] == 0:
                            conn.execute(
                                """
                                UPDATE listings
                                SET status = 'ended',
                                    final_price = COALESCE(final_price, current_price),
                                    last_seen = ?
                                WHERE id = ?
                                """,
                                (utcnow(), listing_id),
                            )

                    conn.execute(
                        """
                        UPDATE auction_hooks
                        SET status = 'fired', fired_at = ?, price = ?, time_remaining = ?, error = NULL
                        WHERE id = ?
                        """,
                        (utcnow(), price, remaining, hook["id"]),
                    )
                    event = {
                        "hook_id": hook["id"],
                        "hook_name": hook["hook_name"],
                        "listing_id": listing_id,
                        "title": hook["title"],
                        "url": hook["url"],
                        "minutes_before_end": hook["minutes_before_end"],
                        "price": price,
                        "time_remaining": remaining,
                    }
                    fired.append(event)
                    _notify_webhook(event)
                except Exception as exc:  # noqa: BLE001
                    conn.execute(
                        """
                        UPDATE auction_hooks
                        SET error = ?, fired_at = ?, status = CASE
                            WHEN scheduled_at <= ? THEN 'missed'
                            ELSE status
                        END
                        WHERE id = ?
                        """,
                        (
                            str(exc),
                            utcnow(),
                            (now - timedelta(minutes=HOOK_FIRE_GRACE_MINUTES))
                            .replace(microsecond=0)
                            .isoformat(),
                            hook["id"],
                        ),
                    )
    finally:
        client.close()
    return fired


def closeout_ended() -> int:
    """After close, keep the last observed bid as the final price."""
    now = datetime.now(timezone.utc)
    closed = 0
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, current_price, auction_end_utc
            FROM listings
            WHERE status = 'active' AND auction_end_utc IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            end = parse_end(row["auction_end_utc"])
            if end is None or now < end:
                continue
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
            closed += 1
            pending = conn.execute(
                """
                SELECT id FROM auction_hooks
                WHERE listing_id = ? AND minutes_before_end = 0 AND status = 'pending'
                """,
                (row["id"],),
            ).fetchone()
            if pending:
                conn.execute(
                    """
                    UPDATE auction_hooks
                    SET status = 'fired', fired_at = ?, price = ?, time_remaining = '0:0:0:0'
                    WHERE id = ?
                    """,
                    (utcnow(), row["current_price"], pending["id"]),
                )
    return closed
