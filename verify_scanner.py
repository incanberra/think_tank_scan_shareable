import os
import sys
import json
import tempfile
import inspect
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import config
import audit_logger
import candidate_selector
import emailer
import rss_parser
import content_extractor
import source_discovery
import topic_utils
import analyzer
import report_generator
import seen_ledger
import main
from reportlab.pdfgen import canvas

_verification_failures = []


def verification_print(*args, **kwargs):
    print(*args, **kwargs)
    if any("[FAIL]" in str(arg) for arg in args):
        _verification_failures.append(" ".join(map(str, args)))


def run_verification():
    _verification_failures.clear()
    print("=== STARTING SCANNER VERIFICATION ===")
    verify_output_dir = os.environ.get(
        "SCANNER_VERIFY_OUTPUT_DIR",
        os.path.join(tempfile.gettempdir(), "think_tanks_scanner_reports_test")
    )
    
    # Test 1: Timezone calculation
    print("\n[Test 1] Timezone and coverage window calculations:")
    try:
        canberra_tz = ZoneInfo(config.TIMEZONE_CANBERRA)
        now_local = datetime.now(canberra_tz)
        print(f"  Local time in Canberra: {now_local.isoformat()}")
        
        # Test start/end boundary math
        run_date_str = now_local.strftime("%Y-%m-%d")
        run_date = datetime.strptime(run_date_str, "%Y-%m-%d")
        end_dt = run_date.replace(hour=3, minute=0, second=0, microsecond=0, tzinfo=canberra_tz)
        start_dt = end_dt - timedelta_stub(48)
        print(f"  Coverage End Time: {end_dt.isoformat()}")
        print(f"  Coverage Start Time: {start_dt.isoformat()}")
        print("  [PASS] Timezone math verified.")
    except Exception as e:
        verification_print(f"  [FAIL] Timezone math failed: {e}")
        
    # Test 2: RSS Date parsing
    print("\n[Test 2] RSS Date string parser tests:")
    rfc822_date = "Sun, 14 Jun 2026 13:00:00 +1000"
    iso_date = "2026-06-14T03:00:00Z"
    
    dt1 = rss_parser.parse_feed_date(rfc822_date)
    dt2 = rss_parser.parse_feed_date(iso_date)
    
    if dt1 and dt1.strftime("%Y-%m-%d") == "2026-06-14":
        print(f"  Parsed RFC 822 successfully: {dt1.isoformat()}")
    else:
        verification_print(f"  [FAIL] Parsing RFC 822 failed: {dt1}")
        
    if dt2 and dt2.strftime("%Y-%m-%d") == "2026-06-14":
        print(f"  Parsed ISO 8601 successfully: {dt2.isoformat()}")
    else:
        verification_print(f"  [FAIL] Parsing ISO 8601 failed: {dt2}")
        
    # Test 3: Deduplication logic
    print("\n[Test 3] Deduplication check:")
    candidates = [
        {"title": "Critical Minerals Report", "url": "https://csis.org/mineral", "institution": "CSIS"},
        {"title": "Critical Minerals Report", "url": "https://csis.org/mineral/", "institution": "CSIS"}, # duplicate title and URL (with slash)
        {"title": "Different Minerals Report", "url": "https://csis.org/mineral", "institution": "CSIS"}, # duplicate URL
        {"title": "Critical Minerals Report", "url": "https://aspi.org.au/mineral", "institution": "ASPI"}, # duplicate title
        {"title": "Unique Report", "url": "https://rand.org/unique", "institution": "RAND"}, # Unique
    ]
    
    deduped = analyzer.deduplicate_items(candidates)
    print(f"  Original candidate count: {len(candidates)}")
    print(f"  Deduplicated candidate count: {len(deduped)}")
    
    # Should keep CSIS title once, ASPI same-title item separately, and unique item.
    if (
        len(deduped) == 3
        and deduped[0]["title"] == "Critical Minerals Report"
        and deduped[1]["institution"] == "ASPI"
        and deduped[2]["title"] == "Unique Report"
    ):
        print("  [PASS] Deduplication logic verified (URL first, source-scoped high-signal title fallback).")
    else:
        verification_print(f"  [FAIL] Deduplication logic failed: {deduped}")

    print("\n[Test 3c] Canonical URL validation and malformed dedupe fallback:")
    fallback_url = "https://www.rusi.org/explore-our-research/publications/research-papers/one"
    bad_soup = BeautifulSoup(
        "<html><head><link rel='canonical' href='https://www.rusi.orghttps://www.rusi.org'></head></html>",
        "html.parser",
    )
    relative_soup = BeautifulSoup(
        "<html><head><link rel='canonical' href='/research/2026/07/report'></head></html>",
        "html.parser",
    )
    canonical_fallback = content_extractor.find_canonical_url(bad_soup, fallback_url)
    canonical_relative = content_extractor.find_canonical_url(relative_soup, "https://example.org/base/page")
    rusi_candidates = [
        {
            "title": "First RUSI high signal report",
            "institution": "Royal United Services Institute (RUSI)",
            "source_domain": "rusi.org",
            "canonical_url": "https://www.rusi.orghttps://www.rusi.org",
            "url": "https://www.rusi.org/explore-our-research/publications/research-papers/first",
        },
        {
            "title": "Second RUSI high signal report",
            "institution": "Royal United Services Institute (RUSI)",
            "source_domain": "rusi.org",
            "canonical_url": "https://www.rusi.orghttps://www.rusi.org",
            "url": "https://www.rusi.org/explore-our-research/publications/research-papers/second",
        },
    ]
    rusi_deduped = analyzer.deduplicate_items(rusi_candidates)
    rusi_identity_values = [seen_ledger.item_identity(item)[1] for item in rusi_candidates]
    bad_key = seen_ledger.identity_key("url", "https://www.rusi.orghttps://www.rusi.org")
    repaired_ledger = seen_ledger.repair_malformed_url_identities(
        {
            "items": {
                bad_key: {
                    "identity_kind": "url",
                    "identity_value": "https://www.rusi.orghttps://www.rusi.org",
                    "urls": [],
                    "report_history": [
                        {
                            "url": "https://www.rusi.org/explore-our-research/publications/research-papers/first",
                        }
                    ],
                }
            }
        }
    )
    repaired_values = [entry.get("identity_value") for entry in repaired_ledger["items"].values()]
    if (
        canonical_fallback == fallback_url
        and canonical_relative == "https://example.org/research/2026/07/report"
        and len(rusi_deduped) == 2
        and len(set(rusi_identity_values)) == 2
        and repaired_values == ["https://www.rusi.org/explore-our-research/publications/research-papers/first"]
    ):
        print("  [PASS] Malformed canonical URLs fall back to item URLs without collapsing reports.")
    else:
        verification_print(
            "  [FAIL] Canonical fallback failed: "
            f"fallback={canonical_fallback}, relative={canonical_relative}, "
            f"deduped={len(rusi_deduped)}, identities={rusi_identity_values}, repaired={repaired_values}"
        )

    print("\n[Test 3b] Source configuration overrides:")
    csis_in_rss = "Center for Strategic and International Studies (CSIS)" in config.RSS_FEEDS
    csis_native = "Center for Strategic and International Studies (CSIS)" in config.SEARCH_ONLY_THINK_TANKS
    orf_active = "Observer Research Foundation (ORF)" in config.THINK_TANKS or any(
        source["name"] == "Observer Research Foundation (ORF)" for source in config.DISCOVERY_SOURCES
    )
    if not csis_in_rss and csis_native and not orf_active:
        print("  [PASS] ORF disabled by default and CSIS uses native homepage discovery.")
    else:
        verification_print(
            "  [FAIL] Source config override failed: "
            f"csis_in_rss={csis_in_rss}, csis_native={csis_native}, orf_active={orf_active}"
        )

    # Test 4: Page extraction and topic ontology hints
    print("\n[Test 4] Page extraction and broad topic hinting:")
    sample_html = """
    <html>
      <head>
        <title>Critical minerals and export controls</title>
        <meta property="article:published_time" content="2026-06-14T02:00:00+10:00">
        <meta name="author" content="Jane Analyst">
      </head>
      <body>
        <nav>Ignore navigation</nav>
        <article>
          <h1>Critical minerals and export controls</h1>
          <p>This report examines critical mineral processing, rare earth supply chains, and export controls.</p>
          <p>It argues allied governments need resilient supply chains for batteries and semiconductors.</p>
        </article>
      </body>
    </html>
    """
    extracted_text = content_extractor.extract_text_from_html(sample_html)
    parsed_dt = content_extractor.parse_date("2026-06-14T02:00:00+10:00")
    hint_item = {
        "title": "Critical minerals and export controls",
        "summary": "Rare earth supply chains and export controls.",
        "extracted_text": extracted_text,
    }
    hints = topic_utils.find_topic_hints(hint_item)
    hint_topics = [hint["topic"] for hint in hints]
    if (
        "critical mineral processing" in extracted_text.lower()
        and parsed_dt
        and "Critical Minerals" in hint_topics
        and "Export Controls and Sanctions" in hint_topics
    ):
        print("  [PASS] Extraction and topic hinting verified.")
    else:
        verification_print(f"  [FAIL] Extraction/topic hinting failed: text={extracted_text[:80]!r}, hints={hint_topics}")

    structured_html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@type": "NewsArticle",
          "headline": "Structured economic security report",
          "articleBody": "This structured article body discusses resilient semiconductor supply chains, cyber risk, and export controls in enough detail to support model review."
        }
        </script>
        <meta name="description" content="Fallback description about supply chain resilience.">
      </head>
      <body><div id="app"></div></body>
    </html>
    """
    structured_text = content_extractor.extract_text_from_html(structured_html)
    if "resilient semiconductor supply chains" in structured_text.lower():
        print("  [PASS] Structured article body extraction fallback verified.")
    else:
        verification_print(f"  [FAIL] Structured extraction fallback failed: {structured_text[:100]!r}")

    # Test 5: Sitemap parsing and native candidate creation
    print("\n[Test 5] Sitemap parsing and native candidate creation:")
    sample_sitemap = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://example.org/research/critical-minerals-report</loc>
        <lastmod>2026-06-14T02:00:00+10:00</lastmod>
      </url>
      <url>
        <loc>https://example.org/about</loc>
        <lastmod>2026-06-14T02:00:00+10:00</lastmod>
      </url>
    </urlset>
    """
    nested, urls = source_discovery.parse_sitemap(sample_sitemap)
    source = {"name": "Example Institute", "domain": "example.org"}
    candidate = source_discovery.candidate_from_url(
        source,
        urls[0]["url"],
        "sitemap",
        date_value=urls[0]["lastmod"],
        query="https://example.org/sitemap.xml",
    )
    sitemap_xml_rejected = source_discovery.native_rejection_reason(
        "https://example.org/sitemaps/research-index.xml",
        allow_dated_listing=True,
    )
    nav_title_rejected = source_discovery.native_rejection_reason(
        "https://example.org/events/briefing",
        title="Current page 1",
    )
    listing_rejected = source_discovery.native_rejection_reason(
        "https://example.org/research",
        title="Fresh policy briefing",
    )
    section_page_rejected = source_discovery.native_rejection_reason(
        "https://www.cnas.org/articles-multimedia",
        title="Articles & Multimedia",
    )
    if (
        not nested
        and len(urls) == 2
        and candidate["discovery_methods"] == ["sitemap"]
        and candidate["date"] == ""
        and candidate["published_at"] is None
        and candidate["modified_date_source"] == "sitemap_lastmod"
        and candidate["modified_at"].startswith("2026-06-14")
        and sitemap_xml_rejected == "native_rejected_sitemap_xml"
        and nav_title_rejected == "native_rejected_navigation_title"
        and listing_rejected == "native_rejected_listing_page"
        and section_page_rejected == "native_rejected_listing_page"
    ):
        print("  [PASS] Sitemap parsing and native candidate creation verified.")
    else:
        verification_print(f"  [FAIL] Sitemap parsing failed: nested={nested}, urls={urls}, candidate={candidate}")

    print("\n[Test 5b] RSS failure native discovery fallback:")
    old_fetch_rss = rss_parser.fetch_rss_items
    old_search_only = source_discovery.discover_search_only_sources
    old_native_sources = source_discovery.discover_native_sources
    old_ddg_fallback = config.ENABLE_DDG_FALLBACK
    try:
        captured_native_names = []

        def fake_fetch_rss(run_date_str, coverage_hours=48):
            return [], {"National Bureau of Economic Research (NBER)": "checked/failed (HTTP 403)"}

        def fake_search_only(run_date_str, coverage_hours=48):
            return [], {}, {"native_rejection_counts": {}, "native_rejected_samples": [], "native_rejected_total": 0}

        def fake_native(run_date_str, coverage_hours=48, source_names=None):
            captured_native_names.extend(source_names or [])
            item = {
                "title": "NBER working paper on strategic supply chains",
                "institution": "National Bureau of Economic Research (NBER)",
                "url": "https://www.nber.org/papers/w12345",
                "source_domain": "nber.org",
                "discovery_methods": ["index_page"],
            }
            return [item], {"National Bureau of Economic Research (NBER)": "checked/native found 1 raw candidates"}, {
                "native_rejection_counts": {},
                "native_rejected_samples": [],
                "native_rejected_total": 0,
            }

        rss_parser.fetch_rss_items = fake_fetch_rss
        source_discovery.discover_search_only_sources = fake_search_only
        source_discovery.discover_native_sources = fake_native
        config.ENABLE_DDG_FALLBACK = False
        fallback_items, fallback_status, fallback_audit = main.stage_discover("2026-06-14")
        fallback_ok = (
            len(fallback_items) == 1
            and "National Bureau of Economic Research (NBER)" in captured_native_names
            and "native fallback" in fallback_status.get("National Bureau of Economic Research (NBER)", "")
            and fallback_audit["native_discovery"]["native_rejected_total"] == 0
        )
        if fallback_ok:
            print("  [PASS] RSS failure triggers source-native fallback discovery.")
        else:
            verification_print(
                "  [FAIL] RSS fallback failed: "
                f"items={fallback_items}, status={fallback_status}, captured={captured_native_names}, audit={fallback_audit}"
            )
    finally:
        rss_parser.fetch_rss_items = old_fetch_rss
        source_discovery.discover_search_only_sources = old_search_only
        source_discovery.discover_native_sources = old_native_sources
        config.ENABLE_DDG_FALLBACK = old_ddg_fallback

    # Test 6: Enrichment cache, PDF extraction, source health, and email subject
    print("\n[Test 6] v4 enrichment cache, PDF extraction, source health, and email subject:")
    try:
        cache_dir = os.path.join(verify_output_dir, "cache")
        old_cache_dir = config.ENRICHMENT_CACHE_DIR
        old_cache_enabled = config.ENABLE_ENRICHMENT_CACHE
        config.ENRICHMENT_CACHE_DIR = cache_dir
        config.ENABLE_ENRICHMENT_CACHE = True

        cached_page = {
            "extraction_status": "ok",
            "http_status": 200,
            "canonical_url": "https://example.org/research/report",
            "extracted_title": "Cached report",
            "extracted_author": "Analyst",
            "extracted_date_raw": "2026-06-14T02:00:00+10:00",
            "extracted_date_dt": content_extractor.parse_date("2026-06-14T02:00:00+10:00"),
            "extracted_date": "14 June 2026",
            "extracted_text": "Critical minerals and export controls.",
            "extracted_text_chars": 39,
            "content_type_guess": "report",
            "source_host": "example.org",
        }
        content_extractor.write_cache_entry(
            "https://example.org/research/report",
            cached_page,
            {"ETag": '"abc123"', "Last-Modified": "Sun, 14 Jun 2026 02:00:00 GMT"},
        )
        cache_entry = content_extractor.load_cache_entry("https://example.org/research/report")
        cached_roundtrip = content_extractor.page_data_from_cache(cache_entry, "hit_fresh")
        bad_cache_path = os.path.join(cache_dir, "bad-canonical.json")
        with open(bad_cache_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "request_url": "https://www.rusi.org/explore-our-research/publications/research-papers/first",
                    "canonical_url": "https://www.rusi.orghttps://www.rusi.org",
                    "page_data": {"canonical_url": "https://www.rusi.orghttps://www.rusi.org"},
                },
                handle,
            )
        cache_repair = content_extractor.repair_enrichment_cache(cache_dir)
        with open(bad_cache_path, "r", encoding="utf-8") as handle:
            repaired_cache_entry = json.load(handle)

        pdf_buffer = BytesIO()
        pdf_canvas = canvas.Canvas(pdf_buffer)
        pdf_canvas.drawString(72, 720, "Critical minerals PDF report")
        pdf_canvas.drawString(72, 700, "This PDF discusses rare earth processing and export controls.")
        pdf_canvas.save()
        pdf_buffer.seek(0)

        class FakeResponse:
            status_code = 200
            url = "https://example.org/reports/minerals.pdf"
            content = pdf_buffer.getvalue()
            headers = {"content-type": "application/pdf"}

        pdf_result = content_extractor.extract_pdf_from_response(FakeResponse(), FakeResponse.url)
        health = audit_logger.build_source_health(
            [candidate],
            [
                {
                    **candidate,
                    "extraction_status": "ok",
                    "date_status": "verified_in_window",
                    "extracted_text": "Critical minerals report",
                    "review_selection_reason": "date_verified_in_window",
                }
            ],
            {"Example Institute": "checked/found 1 item"},
            {"cache_status_counts": {"hit_fresh": 1}},
            {
                "reports": [{"institution": "Example Institute"}],
                "podcasts": [],
                "events": [],
                "excluded": [],
                "needs_review": [],
            },
        )
        subject = emailer.build_email_subject(
            "2026-06-14",
            "deepseek/deepseek-v4-flash",
            {"reports": 2, "podcasts": 1, "events": 1},
        )

        cache_ok = (
            cached_roundtrip.get("cache_status") == "hit_fresh"
            and cached_roundtrip.get("cache_etag") == '"abc123"'
            and cache_repair["files_repaired"] >= 1
            and repaired_cache_entry["canonical_url"]
            == "https://www.rusi.org/explore-our-research/publications/research-papers/first"
        )
        pdf_ok = pdf_result.get("extraction_status") == "ok" and "rare earth" in pdf_result.get("extracted_text", "").lower()
        health_ok = health["sources"] and any(row["source"] == "Example Institute" or row["included"] == 1 for row in health["sources"])
        subject_ok = "14 Jun 2026" in subject and "deepseek v4 flash" in subject and "4 items" in subject

        if cache_ok and pdf_ok and health_ok and subject_ok:
            print("  [PASS] v4 enrichment/reporting helpers verified.")
        else:
            verification_print(
                "  [FAIL] v4 helper checks failed: "
                f"cache_ok={cache_ok}, pdf_ok={pdf_ok}, health_ok={health_ok}, subject_ok={subject_ok}"
            )
        config.ENRICHMENT_CACHE_DIR = old_cache_dir
        config.ENABLE_ENRICHMENT_CACHE = old_cache_enabled
    except Exception as e:
        verification_print(f"  [FAIL] v4 helper checks failed: {e}")
        import traceback
        traceback.print_exc()

    print("\n[Test 7] Persistent first-seen ledger and date cross-check:")
    try:
        ledger_dir = tempfile.mkdtemp(prefix="think_tanks_scanner_ledger_")
        old_ledger_path = config.SEEN_LEDGER_PATH
        config.SEEN_LEDGER_PATH = os.path.join(ledger_dir, "seen_items.json")
        first_run_items = [
            {
                "title": "Undated critical minerals page",
                "institution": "Example Institute",
                "url": "https://example.org/research/undated-critical-minerals?utm_source=test",
                "date_status": "date_unknown",
                "extracted_text": "This page discusses critical minerals and strategic supply chains.",
                "extracted_text_chars": 64,
                "topic_hints": [{"topic": "Critical Minerals", "matched_keywords": ["critical minerals"]}],
            }
        ]
        first_seen_audit = seen_ledger.annotate_items_with_seen_metadata(first_run_items, ledger_dir, "2026-06-14")

        second_run_items = [
            {
                "title": "Undated critical minerals page",
                "institution": "Example Institute",
                "url": "https://example.org/research/undated-critical-minerals",
                "date_status": "date_unknown",
                "extracted_text": "This page discusses critical minerals and strategic supply chains.",
                "extracted_text_chars": 64,
                "topic_hints": [{"topic": "Critical Minerals", "matched_keywords": ["critical minerals"]}],
            }
        ]
        second_seen_audit = seen_ledger.annotate_items_with_seen_metadata(second_run_items, ledger_dir, "2026-06-15")
        should_review, selection_reason = candidate_selector.should_review_candidate(second_run_items[0])

        ledger_ok = (
            first_seen_audit["new_this_run"] == 1
            and second_seen_audit["seen_before"] == 1
            and second_run_items[0]["first_seen_run_date"] == "2026-06-14"
            and second_run_items[0]["date_crosscheck_status"] == "date_unknown_seen_before"
            and not should_review
            and selection_reason == "date_unknown_seen_before_skipped"
            and first_seen_audit["seen_ledger_path"] == config.SEEN_LEDGER_PATH
        )
        if ledger_ok:
            print("  [PASS] First-seen ledger and stale undated filtering verified.")
        else:
            verification_print(
                "  [FAIL] First-seen ledger failed: "
                f"first={first_seen_audit}, second={second_seen_audit}, "
                f"item={second_run_items[0]}, selection={selection_reason}, should_review={should_review}"
            )
        config.SEEN_LEDGER_PATH = old_ledger_path
    except Exception as e:
        verification_print(f"  [FAIL] First-seen ledger check failed: {e}")
        import traceback
        traceback.print_exc()

    print("\n[Test 8] v5 ledger backfill, content-change review, and report-history ordering:")
    try:
        ledger_dir = tempfile.mkdtemp(prefix="think_tanks_scanner_v5_ledger_")
        output_dir = tempfile.mkdtemp(prefix="think_tanks_scanner_v5_reports_")
        audit_dir = os.path.join(output_dir, "audit")
        os.makedirs(audit_dir, exist_ok=True)
        old_ledger_path = config.SEEN_LEDGER_PATH
        config.SEEN_LEDGER_PATH = os.path.join(ledger_dir, "seen_items.json")

        historical_item = {
            "title": "Historical critical minerals page",
            "institution": "Example Institute",
            "url": "https://example.org/research/historical-critical-minerals",
            "date_status": "date_unknown",
            "extracted_text": "Historical critical minerals text.",
            "extracted_text_chars": 34,
        }
        for date_value in ["2026-06-10", "2026-06-12"]:
            with open(os.path.join(audit_dir, f"enriched_candidates_{date_value}.jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps(historical_item) + "\n")
        backfill = seen_ledger.backfill_seen_ledger_from_audits(output_dir)
        ledger = seen_ledger.load_ledger(output_dir)
        key = seen_ledger.identity_key(*seen_ledger.item_identity(historical_item))

        changed_items = [
            {
                "title": "Undated critical minerals page",
                "institution": "Example Institute",
                "url": "https://example.org/research/undated-critical-minerals",
                "date_status": "date_unknown",
                "extracted_text": "Original critical minerals text.",
                "extracted_text_chars": 32,
                "topic_hints": [{"topic": "Critical Minerals", "matched_keywords": ["critical minerals"]}],
            }
        ]
        seen_ledger.annotate_items_with_seen_metadata(changed_items, output_dir, "2026-06-14")
        changed_items_second = [
            {
                "title": "Undated critical minerals page",
                "institution": "Example Institute",
                "url": "https://example.org/research/undated-critical-minerals",
                "date_status": "date_unknown",
                "extracted_text": "Updated critical minerals text with new export controls analysis.",
                "extracted_text_chars": 62,
                "topic_hints": [{"topic": "Critical Minerals", "matched_keywords": ["critical minerals"]}],
            }
        ]
        changed_audit = seen_ledger.annotate_items_with_seen_metadata(changed_items_second, output_dir, "2026-06-15")
        should_review_changed, changed_reason = candidate_selector.should_review_candidate(changed_items_second[0])

        stage_source = inspect.getsource(main.stage_analyze_and_render)
        render_before_reported = stage_source.find("render_model_outputs(") < stage_source.find("seen_ledger.mark_reported_items(")

        v5_ok = (
            backfill["files_processed"] == 2
            and ledger["items"][key]["first_seen_run_date"] == "2026-06-10"
            and changed_audit["date_crosscheck_counts"].get("content_hash_changed") == 1
            and changed_items_second[0]["date_crosscheck_status"] == "date_unknown_seen_before_content_changed"
            and should_review_changed
            and changed_reason == "date_unknown_seen_before_content_changed"
            and render_before_reported
        )
        if v5_ok:
            print("  [PASS] v5 ledger backfill, content-change selection, and render/report ordering verified.")
        else:
            verification_print(
                "  [FAIL] v5 ledger checks failed: "
                f"backfill={backfill}, changed_audit={changed_audit}, item={changed_items_second[0]}, "
                f"reason={changed_reason}, should_review={should_review_changed}, render_before_reported={render_before_reported}"
            )
        config.SEEN_LEDGER_PATH = old_ledger_path
    except Exception as e:
        verification_print(f"  [FAIL] v5 ledger/backfill check failed: {e}")
        import traceback
        traceback.print_exc()

    print("\n[Test 8b] Date-poor source sampling:")
    old_sample = config.DATE_POOR_SOURCE_SAMPLE_PER_RUN
    old_threshold = config.DATE_POOR_SOURCE_THRESHOLD
    old_min_chars = config.DATE_POOR_SAMPLE_MIN_TEXT_CHARS
    try:
        config.DATE_POOR_SOURCE_SAMPLE_PER_RUN = 1
        config.DATE_POOR_SOURCE_THRESHOLD = 0.8
        config.DATE_POOR_SAMPLE_MIN_TEXT_CHARS = 120
        date_poor_items = []
        for idx in range(5):
            date_poor_items.append(
                {
                    "title": f"Date poor official report {idx}",
                    "institution": "Date Poor Institute",
                    "source_domain": "example.org",
                    "url": f"https://example.org/research/report-{idx}",
                    "canonical_url": f"https://example.org/research/report-{idx}",
                    "item_type": "report",
                    "date_status": "date_unknown",
                    "seen_status": "new_this_run" if idx == 0 else "seen_before",
                    "content_changed_since_last_seen": False,
                    "extracted_text": "This official report has enough extracted text for a sampled review. " * 3,
                    "extracted_text_chars": 190,
                    "topic_hints": [],
                    "discovery_methods": ["index_page"],
                }
            )
        sampled, sample_audit = candidate_selector.select_candidates_for_review(date_poor_items, max_per_source=5)
        sample_ok = (
            len(sampled) == 1
            and sampled[0]["review_selection_reason"] == "date_poor_source_new_or_changed_sample"
            and sample_audit["date_poor_sample_counts"].get("Date Poor Institute") == 1
        )
        if sample_ok:
            print("  [PASS] Date-poor sources send a bounded new-item sample to model review.")
        else:
            verification_print(f"  [FAIL] Date-poor sampling failed: sampled={sampled}, audit={sample_audit}")
    except Exception as e:
        verification_print(f"  [FAIL] Date-poor sampling check failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        config.DATE_POOR_SOURCE_SAMPLE_PER_RUN = old_sample
        config.DATE_POOR_SOURCE_THRESHOLD = old_threshold
        config.DATE_POOR_SAMPLE_MIN_TEXT_CHARS = old_min_chars
        
    # Test 9: Report formatting & PDF rendering using mock data
    print("\n[Test 9] Report generators verification (Markdown, HTML, PDF):")
    mock_data = {
        "reports": [
            {
                "title": "US Export Controls on Advanced Semiconductors",
                "institution": "Center for Strategic and International Studies (CSIS)",
                "date": "14 June 2026",
                "author": "Gregory C. Allen",
                "tags": ["Export Controls and Sanctions", "Emerging Technologies"],
                "summary": "This briefing analyzes the strategic impact of updated US export controls on advanced semiconductor tech. It concludes that export restrictions are slowing China's indigenous AI chip manufacturing capability, though it is driving domestic workarounds.",
                "why_it_matters": "Shows concrete impacts of export controls on chip supply chains.",
                "importance_score": 5,
                "url": "https://www.csis.org/analysis/us-export-controls",
                "relevance_confidence": "high",
                "evidence": [
                    "The item discusses export controls on advanced semiconductors.",
                    "It describes effects on China's AI chip manufacturing capability."
                ],
                "date_status": "verified_in_window",
                "extraction_status": "ok",
                "discovery_methods": ["rss"]
            },
            {
                "title": "Securing Critical Mineral Supply Chains",
                "institution": "Australian Strategic Policy Institute (ASPI)",
                "date": "13 June 2026",
                "author": "Dr. Rajah Al-Khafaji",
                "tags": ["Critical Minerals", "Reshoring and Friendshoring"],
                "summary": "This report examines Australia's opportunities to diversify rare earths refinement. It argues that allied co-investment in processing hubs is critical to break the processing monopoly currently held by China.",
                "why_it_matters": "Proposes actionable supply chain security policies for key mineral nodes.",
                "importance_score": 4,
                "url": "https://www.aspi.org.au/report/securing-critical-minerals",
                "relevance_confidence": "high",
                "evidence": ["The report focuses on rare earths refinement and allied co-investment."],
                "date_status": "verified_in_window",
                "extraction_status": "ok",
                "discovery_methods": ["rss"]
            }
        ],
        "events": [
            {
                "title": "Webinar: Debt-Trap Diplomacy in the Pacific",
                "institution": "Lowy Institute",
                "date": "15 June 2026",
                "event_time": "11:00 AM AEST",
                "author": "Meg Keen, Jessica Collins",
                "tags": ["Sovereign Debt and Debt-Trap Diplomacy", "Economic Coercion"],
                "summary": "An upcoming panel discussion on infrastructure lending and sovereign debt exposure in Pacific Island states.",
                "why_it_matters": "Monitors regional debt structures and foreign influence vectors.",
                "importance_score": 3,
                "url": "https://www.lowyinstitute.org/events/debt-trap-pacific",
                "relevance_confidence": "medium",
                "evidence": ["The event covers infrastructure lending and sovereign debt exposure."],
                "date_status": "verified_in_window",
                "extraction_status": "ok",
                "discovery_methods": ["rss"]
            }
        ],
        "podcasts": [
            {
                "title": "Episode 44: Emerging Tech and FDI Screening",
                "institution": "War on the Rocks",
                "date": "12 June 2026",
                "author": "Ryan Evans",
                "tags": ["Foreign Direct Investment (FDI) Screening", "Emerging Technologies"],
                "summary": "A podcast discussing the expanding mandates of CFIUS and allied screening bodies for cross-border AI start-up investments.",
                "why_it_matters": "Outlines new regulatory boundaries for emerging technology investment flows.",
                "importance_score": 3,
                "url": "https://warontherocks.com/podcasts/fdi-screening-tech",
                "relevance_confidence": "medium",
                "evidence": ["The episode discusses CFIUS and allied screening bodies."],
                "date_status": "verified_in_window",
                "extraction_status": "ok",
                "discovery_methods": ["rss"]
            }
        ]
    }
    
    mock_status = {
        "Center for Strategic and International Studies (CSIS)": "checked/found 1 item",
        "Australian Strategic Policy Institute (ASPI)": "checked/found 1 item",
        "Lowy Institute": "checked/found 1 item",
        "War on the Rocks": "checked/found 1 item",
        "RAND Corporation": "checked/native no raw candidates",
        "Stockholm International Peace Research Institute (SIPRI)": "checked/native no raw candidates"
    }

    mock_recall_audit = {
        "recall_risk_flags": ["2 sources returned no candidates"],
        "recall_risk_details": [
            {
                "severity": "medium",
                "area": "RAND Corporation",
                "issue": "No candidates discovered",
                "status": "checked/native no raw candidates",
                "implication": "Native discovery found no raw candidates; this can be normal, but it is a recall watch item.",
            }
        ],
        "raw_candidate_summary": {"total_candidates": 12},
        "enriched_candidate_summary": {"total_candidates": 10},
        "review_selection": {
            "selected_for_review": 5,
            "reason_counts": {
                "skipped:date_unknown_seen_before_skipped": 2,
                "selected:date_unknown_seen_before_content_changed": 1,
            },
        },
        "seen_ledger": {"new_this_run": 8, "seen_before": 2, "backfill": {"completed_at": "2026-06-14T00:00:00Z", "files_processed": 3}},
    }
    
    # Create temp directory outside OneDrive to avoid file-lock noise.
    os.makedirs(verify_output_dir, exist_ok=True)
    
    try:
        # Save JSON
        test_json_path = os.path.join(verify_output_dir, "test_report.json")
        with open(test_json_path, "w", encoding="utf-8") as f:
            json.dump(mock_data, f, indent=2)
            
        # Generate Markdown
        markdown_data = {
            "reports": [dict(x) for x in mock_data["reports"]],
            "podcasts": [dict(x) for x in mock_data["podcasts"]],
            "events": [dict(x) for x in mock_data["events"]]
        }
        md_content = report_generator.generate_markdown(markdown_data, "2026-06-14", mock_status, mock_recall_audit)
        with open(os.path.join(verify_output_dir, "test_report.md"), "w", encoding="utf-8") as f:
            f.write(md_content)
        print("  [+] Markdown test generated.")
        
        # Generate HTML
        html_content = report_generator.generate_html(mock_data, "2026-06-14", mock_status, json.dumps(mock_data), mock_recall_audit)
        with open(os.path.join(verify_output_dir, "test_report.html"), "w", encoding="utf-8") as f:
            f.write(html_content)
        print("  [+] HTML test generated.")
        
        # Generate PDF
        pdf_path = os.path.join(verify_output_dir, "test_report.pdf")
        report_generator.generate_pdf(mock_data, "2026-06-14", mock_status, pdf_path, mock_recall_audit)
        print(f"  [+] PDF test generated at: {pdf_path}")
        markings_removed = all(
            phrase not in (md_content + html_content)
            for phrase in ["Official Use Only", "Classified", "National Security Information"]
        )
        if "## Scan Quality" in md_content and "source-native sitemaps" in md_content and "Scan Quality" in html_content and markings_removed:
            print("  [PASS] Report layout generation verified successfully.")
        else:
            verification_print("  [FAIL] Report layout missing scan quality, methodology, or marking removal.")
        
        # Test 10: Comparison report layout rendering
        print("\n[Test 10] Comparison report layout rendering verification:")
        mock_results_by_model = {
            "google/gemini-2.5-flash": mock_data,
            "meta-llama/llama-3-8b-instruct": {
                "reports": [
                    mock_data["reports"][0], # Llama only selected the first report
                ],
                "podcasts": [], # Llama filtered out podcasts
                "events": [
                    mock_data["events"][0] # and event
                ]
            }
        }
        
        comp_md = report_generator.generate_comparison_markdown(mock_results_by_model, "2026-06-14")
        with open(os.path.join(verify_output_dir, "test_report_comparison.md"), "w", encoding="utf-8") as f:
            f.write(comp_md)
        print("  [+] Markdown Comparison test report generated.")
        
        comp_html = report_generator.generate_comparison_html(mock_results_by_model, "2026-06-14")
        with open(os.path.join(verify_output_dir, "test_report_comparison.html"), "w", encoding="utf-8") as f:
            f.write(comp_html)
        print("  [+] HTML Comparison test report generated.")
        print("  [PASS] Comparison layout generation verified successfully.")
        
    except Exception as e:
        verification_print(f"  [FAIL] Layout generation failed: {e}")
        import traceback
        traceback.print_exc()
        
    print("\n=== VERIFICATION COMPLETED ===")
    if _verification_failures:
        raise SystemExit(1)

def timedelta_stub(hours):
    from datetime import timedelta
    return timedelta(hours=hours)

if __name__ == "__main__":
    run_verification()
