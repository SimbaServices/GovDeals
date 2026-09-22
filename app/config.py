from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("GOVDEALS_DB", DATA_DIR / "govdeals.db"))

# Anonymous storefront keys shipped by www.govdeals.com (same values the
# browser sends). Override with env vars if Liquidity Services rotates them.
MAESTRO_URL = os.environ.get("GOVDEALS_MAESTRO_URL", "https://maestro.lqdt1.com")
MAESTRO_API_KEY = os.environ.get(
    "GOVDEALS_MAESTRO_API_KEY", "af93060f-337e-428c-87b8-c74b5837d6cd"
)
OCP_APIM_KEY = os.environ.get(
    "GOVDEALS_OCP_APIM_KEY", "cf620d1d8f904b5797507dc5fd1fdb80"
)
BUSINESS_ID = "GD"
SITE_ID = "1"
SITE_ORIGIN = "https://www.govdeals.com"

USER_AGENT = os.environ.get(
    "GOVDEALS_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
)

# Full-catalog scans. 4 hours = 6 times per day.
SCRAPE_INTERVAL_HOURS = float(os.environ.get("GOVDEALS_SCRAPE_HOURS", "4"))
HOOK_POLL_SECONDS = float(os.environ.get("GOVDEALS_HOOK_POLL_SECONDS", "30"))
CLOSEOUT_MINUTES = float(os.environ.get("GOVDEALS_CLOSEOUT_MINUTES", "15"))

REQUEST_PAUSE_SECONDS = float(os.environ.get("GOVDEALS_REQUEST_PAUSE", "0.45"))
DETAIL_PAUSE_SECONDS = float(os.environ.get("GOVDEALS_DETAIL_PAUSE", "0.55"))
PAGE_SIZE = 120

# Hooks in the final 2 hours, then a tighter countdown through close.
HOOK_OFFSETS_MINUTES = (120, 90, 60, 30, 15, 5, 1, 0)
HOOK_FIRE_GRACE_MINUTES = 12

WEBHOOK_URL = os.environ.get("GOVDEALS_WEBHOOK_URL", "").strip()

HOST = os.environ.get("GOVDEALS_HOST", "127.0.0.1")
PORT = int(os.environ.get("GOVDEALS_PORT", "8765"))

# Category browse uses the same Solr facet filters as the live category pages.
SEARCHES = (
    {
        "watch_category": "skid_steer",
        "label": "Skid Steers",
        "referer_path": "/en/skid-steers",
        "category_ids": ("t4", "36", "36A"),
        "require_fivek": False,
    },
    {
        "watch_category": "forklift_5k",
        "label": "Forklifts",
        "referer_path": "/en/forklifts",
        "category_ids": ("t5", "251", "142"),
        "require_fivek": True,
    },
    {
        "watch_category": "forklift_5k",
        "label": "Rough Terrain Forklifts",
        "referer_path": "/en/forklifts-rough-terrain",
        "category_ids": ("t4", "36", "36Q"),
        "require_fivek": True,
    },
)
