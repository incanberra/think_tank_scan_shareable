# Architecture

## Pipeline

1. `main.py` resolves run date, model selection, output directory, and email behaviour.
2. `rss_parser.py` fetches RSS entries for sources with reliable feeds.
3. `source_discovery.py` performs source-native discovery from sitemaps and index pages.
4. `ddg_searcher.py` can optionally perform unpaid DuckDuckGo fallback discovery.
5. `content_extractor.py` enriches candidate URLs by fetching pages or PDFs and extracting text and metadata.
6. `seen_ledger.py` checks persistent first-seen, last-seen, content hash, report history, and delivery history.
7. `candidate_selector.py` applies deterministic eligibility rules from `eligibility.py` before choosing candidates for LLM review.
8. `analyzer.py` deduplicates candidates and asks OpenRouter to classify relevance.
9. `main.py` checks persisted report history and date/event eligibility again before `report_generator.py` renders JSON, Markdown, HTML, and PDF outputs.
10. `emailer.py` sends the primary PDF report by SMTP.
11. `audit_logger.py` writes raw, enriched, decision, recall, and source-health audit files.

## External Calls

The scanner makes external calls in four places:

- RSS HTTP requests from `rss_parser.py`.
- Sitemap, index-page, HTML, and PDF HTTP requests from `source_discovery.py` and `content_extractor.py`.
- OpenRouter API calls from `ai_client.py` during LLM relevance review.
- Gmail SMTP calls from `emailer.py` when email delivery is enabled.

The backfill command does not call external APIs. It reads existing local audit files and updates the seen ledger.

## State And Audit Files

The seen ledger is separate from report audit files:

- Ledger: `.scanner_state/seen_items.json`
- Audit files: `reports/audit/`

The ledger is used to avoid repeatedly reporting unchanged items and to make publication-date uncertainty auditable. Audit files explain what was discovered, enriched, skipped, reviewed, included, and excluded for each run.

`state_storage.py` holds an OS-backed lock for the scan and for ledger transactions.
It saves JSON through a flushed temporary file and atomic replacement. The prior
valid ledger is retained as `.bak`; corrupt or missing established state stops a run.
The JSON format remains compatible with existing history, with version-3 metadata
for scan identities and observed URL aliases.

Publication provenance is explicit (`date_source`); modification evidence lives in
`modified_at` and `modified_date_source`. Event start/end fields are independent of
publication dates. The final gate cannot use an LLM-generated date as evidence.

Offline regression coverage is in `test_scanner_reliability.py`. Run it with
`python -m unittest test_scanner_reliability -v`, followed by `python verify_scanner.py`
for the existing extraction and report-layout checks. Both commands fail with a
nonzero exit code if a check fails.


## Run orchestration and report design

`scan_runtime.ScanRun`, under the existing process lock, owns durable workflow state
and the timestamp/UUID output directory. A ContextVar supplies source-specific
coverage windows to discovery and date assessment. A completed manifest and latest
pointer are written only after successful execution (or explicit no-email mode);
failed runs preserve diagnostics and do not advance checkpoints.

`publication_dates` extracts publisher dates; `http_client` bounds transient
retries and records timing; `evidence_selection` composes article evidence packets.
`candidate_selector` records an explicit selection boolean for each item.
`analyzer` matches decisions by temp_id, caches validated responses, and enforces
publication eligibility separately. Pending verification survives observation in
the seen ledger and remains separate from reported/delivered history.

`editorial_report` supplies shared content for static Jinja email markup in
`templates/editorial_report.html` and paginated ReportLab PDF output. User-controlled
HTML is escaped and article links are limited to HTTP(S). The Markdown and model
comparison formats remain supported. Browser previews verify responsive light/dark
layout; email-client rendering still depends on each client's CSS support.
