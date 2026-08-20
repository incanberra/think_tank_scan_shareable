# Adding Or Removing Sources

Sources live in `config.py`.

## Disable A Source

Set `DISABLED_THINK_TANKS` in `.env` using exact source names separated with `|`:

```text
DISABLED_THINK_TANKS=Observer Research Foundation (ORF)|Example Institute
```

This is the safest way to remove noisy sources without editing code.

## Add A Source With RSS

Add an entry to `DISCOVERY_SOURCES`:

```python
{
    "name": "Example Institute",
    "domain": "example.org",
    "rss_url": "https://www.example.org/feed/",
    "methods": ["rss", "page_extract"],
}
```

RSS is preferred when the feed is current and includes useful publication dates.

## Add A Source Without RSS

Use source-native index discovery:

```python
{
    "name": "Example Institute",
    "domain": "example.org",
    "base_url": "https://www.example.org/",
    "index_paths": ["/research", "/publications", "/events"],
    "methods": ["page_extract"],
}
```

Choose index paths that list actual reports, commentary, events, or analysis. Avoid broad navigation pages unless they reliably expose recent content.

## When To Use DDG

DuckDuckGo fallback is unpaid but less reliable. Enable it only when source-native discovery cannot find useful candidates:

```text
ENABLE_DDG_FALLBACK=true
```

For a source to use DDG, include `ddg` in its `methods` list.

## Validate Changes

After changing sources:

```powershell
python -m compileall main.py analyzer.py audit_logger.py candidate_selector.py report_generator.py seen_ledger.py source_discovery.py verify_scanner.py
python verify_scanner.py
python main.py --no-email
```

Check `reports/audit/recall_audit_YYYY-MM-DD.json` to see whether the new source produced raw candidates, enriched candidates, and reviewed items.
