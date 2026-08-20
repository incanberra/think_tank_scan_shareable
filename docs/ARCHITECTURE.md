# Architecture

## Pipeline

1. `main.py` resolves run date, model selection, output directory, and email behaviour.
2. `rss_parser.py` fetches RSS entries for sources with reliable feeds.
3. `source_discovery.py` performs source-native discovery from sitemaps and index pages.
4. `ddg_searcher.py` can optionally perform unpaid DuckDuckGo fallback discovery.
5. `content_extractor.py` enriches candidate URLs by fetching pages or PDFs and extracting text and metadata.
6. `seen_ledger.py` checks persistent first-seen, last-seen, content hash, report history, and delivery history.
7. `candidate_selector.py` decides which enriched candidates should reach LLM review.
8. `analyzer.py` deduplicates candidates and asks OpenRouter to classify relevance.
9. `report_generator.py` renders JSON, Markdown, HTML, and PDF outputs.
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
