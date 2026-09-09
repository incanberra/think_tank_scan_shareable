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
python -m unittest test_scanner_reliability -v
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

## New-item Eligibility And State

Previously included items are excluded before model review and checked again against
the ledger before report rendering. A changed date, changed content, RSS rediscovery,
or a rerun on the same day does not make a reported item new. Candidates that were
discovered but never included can still be retried. Comparison models may include
the same new item within one scan.

Publication dates are kept separate from modification dates. HTTP `Last-Modified`,
sitemap `lastmod`, Atom `updated`, and PDF metadata timestamps cannot establish a
new publication. Undated publications are held out of the new-publications report;
the model can assess their relevance for review. Events require a verified future
start date from structured page metadata and are announced once. Past events,
event directories, and known programme/project landing pages are excluded.

The ledger path is resolved relative to the scanner's code directory, regardless
of the shell's working directory. Use an absolute `SEEN_LEDGER_PATH` when multiple
checkouts should share history. Saves use atomic replacement and retain the prior
valid state in `seen_items.json.bak`. A process lock prevents overlapping scans
using the same ledger. An unreadable ledger stops the scan rather than silently
starting with empty history. Stop scans and restore a validated ledger backup if
this happens; a backup can lag the last successful save, so reconcile newer report
history before resuming.

Existing version-2 history is preserved and normalized on load; version 3 is saved
on the next run. Observed request/redirect/canonical aliases retain prior report
history. Old enrichment caches are refetched because their date provenance cannot
be trusted. No ledger reset is required.

These changes do not add automatic failed-email retries or alter the scheduled
coverage window. A `--no-email` run still records rendered items as reported.

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


## Durable runs and verification recovery

Every scan now writes `reports/runs/<timestamp-id>/`, including the report, audit,
`run.json` manifest and `pending_review.json` snapshot. `reports/latest.json` points
to the most recent completed run. Same-day runs preserve all previous editions.
The HTML email and PDF use the editorial report design; reruns following a
successful email are labelled supplements with the previously delivered count.

State beside the configured seen ledger includes `workflow_state.json`: unresolved
verification work, deferred discovery candidates, relevance decisions and source
checkpoints. Keep this state, its backup and the original seen ledger across runs.
Missing or corrupt state with an existing backup fails explicitly; restore a
validated backup before running again. Backups are replaced atomically.

```powershell
# Include publications up to the current Canberra time:
python main.py --as-of now
# Reassess relevance with fresh decisions (reported items remain suppressed):
python main.py --reprocess
# Make pending/paused verification cases eligible again, within per-source limits:
python main.py --retry-pending
# Offline regression and report generation checks:
python -m unittest test_scanner_reliability test_scan_workflow -v
python verify_scanner.py
```

Pending cases retry up to five per source per run, with 1, 2, 4, then 7-day delays.
After six unsuccessful attempts they stay paused for attention rather than vanish.
Thin extracts and publisher-declared paywalls require verification; no configured
model/API produces pending decisions rather than unreviewed inclusions. Public
same-publisher PDF links can recover evidence without supplying a publication date
for the landing page. Priority date adapters cover CSIS, RUSI, CNAS and SIPRI.

Native discovery removes reported items before its quota and retains overflow for
later runs, oldest deferred first. A 1,000-item source backlog ceiling fails
explicitly rather than silently dropping overflow. Successful source checkpoints
extend the next window across gaps with six hours of overlap; sources with discovery
failures do not advance. This improves coverage but cannot guarantee recall if a
publisher removes old entries from its feed/index. Historical `--date` runs use the
fixed window and do not advance checkpoints. The default cutoff remains 03:00
Canberra time for the scheduled 05:00 scan.

Decision reuse depends on content, publication evidence, model, topic guidance and
prompt version. The model receives opening, relevant middle sections and conclusion
within a 12,000-character default evidence budget. Candidate IDs map responses
independently of order; missing/duplicate decisions remain pending. Audit counts
separate selected candidates, cache hits, model submissions and review outcomes.

The first run after upgrading refreshes the old extraction cache and initializes
workflow state. It may take longer. Existing duplicate history is preserved.


### 10 September reliability follow-up

The extractor preserves structural page/article containers when CSS names mention
cookies or header spacing. This fixes summary-only extraction on Atlantic Council
and CFR. Extraction cache schema 4 refreshes previously damaged entries. Native
index discovery excludes navigation before its link budget and removes known
programme/listing candidates before its source quota.

OpenRouter JSON requests now allow 120 seconds for a response, retry transport
timeouts/connection failures, require parameter-compatible providers, and reject
truncated completions. Per-request audit records include actual provider/model,
response ID, timing, token usage and reported cost when the API supplies them.
Permanent authentication failures are not retried. The configured model is unchanged.
Run `python -m unittest test_scanner_reliability test_scan_workflow test_ai_client -v`.
