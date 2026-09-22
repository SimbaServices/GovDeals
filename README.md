# GovDeals Equipment Tracker

Tracks **skid steer** and **5,000 lb forklift** auctions on [GovDeals](https://www.govdeals.com). It scans the live catalog several times a day, stores item / description / price / time remaining (including the final price), and assigns **individual hooks through the last 2 hours** of each auction.

## What it stores

- Item title, make/model/year, seller, location
- Full lot description
- Current bid and a price history
- Time remaining and auction end
- Final price when the lot closes
- Eight per-lot hooks: `t_minus_2h`, `t_minus_1h30m`, `t_minus_1h`, `t_minus_30m`, `t_minus_15m`, `t_minus_5m`, `t_minus_1m`, `final_close`

## Run it

```powershell
cd C:\Simba\GovDeals
python -m pip install -r requirements.txt
python run.py scrape
python run.py serve
```

Then open http://127.0.0.1:8765

| Command | Purpose |
| --- | --- |
| `python run.py serve` | Dashboard plus scheduler (default) |
| `python run.py scrape` | One catalog scan |
| `python run.py hooks` | Assign and fire any due end-of-auction hooks |
| `python run.py web` | Dashboard only, no background jobs |

Scans default to every 4 hours (6 times a day). Hook due-times are checked every 30 seconds. Optional outbound webhook:

```powershell
$env:GOVDEALS_WEBHOOK_URL = "https://example.com/auction-hook"
```

SQLite data lives in `data/govdeals.db`.
