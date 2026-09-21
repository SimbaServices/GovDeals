from __future__ import annotations

import argparse
import json

from app.db import init_db
from app.hooks import assign_hooks_for_active, fire_due_hooks
from app.scraper import scrape_once
from app.web import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="GovDeals skid steer and 5k forklift tracker")
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=("serve", "web", "scrape", "hooks"),
        help="serve = dashboard + scheduler (default)",
    )
    args = parser.parse_args()
    init_db()

    if args.command == "scrape":
        print(json.dumps(scrape_once(), indent=2))
        return
    if args.command == "hooks":
        assigned = assign_hooks_for_active()
        fired = fire_due_hooks()
        print(json.dumps({"assigned": assigned, "fired": fired}, indent=2))
        return
    if args.command == "web":
        serve(enable_scheduler=False)
        return
    serve(enable_scheduler=True)


if __name__ == "__main__":
    main()
