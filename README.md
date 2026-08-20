# Think Tank Scanner

Think Tank Scanner discovers recent reports, analysis, events, podcasts, and videos from a curated set of foreign policy and economic security sources. It enriches candidate pages, checks them against a persistent seen ledger, asks an OpenRouter model to assess relevance, then produces JSON, Markdown, HTML, and PDF reports. Successful runs send the primary PDF report by email unless `--no-email` is used.

## What It Checks

The scanner combines several discovery methods:

- RSS feeds where sources provide useful feeds.
- Source-native sitemap and index-page discovery for sources without reliable RSS.
- Optional DuckDuckGo fallback, disabled by default because it can be unreliable.
- Page and PDF enrichment to extract title, publication date, metadata, and text.
- A seen ledger to distinguish new, previously seen, and materially changed items.
- LLM relevance review against the scanner's topic set.

Generated reports include scan-quality and recall-risk sections so readers can see what was found, skipped, reviewed, and excluded.

## Quick Start

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env
python verify_scanner.py
python main.py --no-email
```

Edit `.env` before running a live scan. At minimum, set `OPENROUTER_API_KEY`. To send email, also set the SMTP fields.

## Running Scans

Run with the model configured in `.env`:

```powershell
python main.py
```

Run without email:

```powershell
python main.py --no-email
```

Run a specific model:

```powershell
python main.py --model openai/gpt-5.6-luna
```

Run for a specific date:

```powershell
python main.py --date 2026-08-21
```

Backfill the seen ledger from historical enriched candidate audits:

```powershell
python main.py --backfill-seen-ledger
```

## Important Configuration

Configuration lives in `.env`.

- `OPENROUTER_API_KEY`: required for model review.
- `OPENROUTER_MODEL`: default model for normal and scheduled scans.
- `SMTP_SENDER_EMAIL`: Gmail sender address.
- `SMTP_SENDER_PASSWORD`: Gmail App Password.
- `SMTP_RECEIVER_EMAIL`: comma-separated recipient list.
- `SEEN_LEDGER_PATH`: persistent duplicate/date ledger path, default `.scanner_state/seen_items.json`.
- `DISABLED_THINK_TANKS`: exact source names to exclude, separated by `|`.
- `ENABLE_DDG_FALLBACK`: optional unpaid DDG fallback; default `false`.

## Outputs

Normal runs write to `reports/`:

- `report_YYYY-MM-DD_model.json`
- `report_YYYY-MM-DD_model.md`
- `report_YYYY-MM-DD_model.html`
- `report_YYYY-MM-DD_model.pdf`
- `reports/audit/*.json` and `*.jsonl` audit files

Local runtime state is written to:

- `.scanner_state/seen_items.json`
- `.cache/enrichment/`
- `logs/`

These folders are ignored by Git.

## Scheduling On Windows

The included scripts can register a Windows Scheduled Task that runs Tuesday to Friday at 5:00am:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_scheduled_scan.ps1
```

The scheduled task calls `scripts\run_scheduled_scan.cmd`, which runs `python -u main.py` from the repository root. It uses `OPENROUTER_MODEL` from `.env`.

For best reliability, open Task Scheduler as Administrator, edit the task, and tick **Run with highest privileges**.

See [docs/SCHEDULING_WINDOWS.md](docs/SCHEDULING_WINDOWS.md) for troubleshooting.

## Customising Sources

Sources are configured in `config.py` in `DISCOVERY_SOURCES`.

Each source can use:

- `rss_url` for RSS discovery.
- `base_url` and `index_paths` for source-native index-page discovery.
- `methods` such as `rss`, `page_extract`, and optionally `ddg`.

See [docs/ADDING_SOURCES.md](docs/ADDING_SOURCES.md) before adding or removing sources.

## Sample Output

See `sample_output/` for a small sanitized example report and recall audit. These are illustrative only and do not contain live scan results.
