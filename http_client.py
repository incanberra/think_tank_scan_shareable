"""Bounded retries and request metrics for public source requests."""
import time
import requests
import scan_runtime
from urllib.parse import urlparse
from pathlib import Path
import sys


def browser_get(url, **kwargs):
    """Fetch public pages with browser TLS; preserve requests response semantics."""
    try:
        from curl_cffi import requests as browser_requests
    except ImportError:
        vendor = Path(__file__).resolve().parent / '.vendor'
        if vendor.is_dir():
            sys.path.insert(0, str(vendor))
        from curl_cffi import requests as browser_requests
    if (urlparse(url).hostname or '') in {'cnas.org', 'www.cnas.org'}:
        kwargs['headers'] = {k: v for k, v in kwargs.get('headers', {}).items() if k.lower() != 'user-agent'}
    try:
        result = browser_requests.get(url, impersonate='chrome', **kwargs)
    except browser_requests.exceptions.RequestException as exc:
        raise requests.ConnectionError(str(exc)) from exc
    response = requests.Response()
    response.status_code = result.status_code
    response._content = result.content
    response.headers.update(result.headers)
    response.url = result.url
    response.encoding = result.encoding
    if (urlparse(url).hostname or "").endswith("iiss.org") and "text/html" in response.headers.get("content-type", ""):
        from iiss_public import render_public_components
        response._content = render_public_components(response.text).encode("utf-8")
        response.encoding = "utf-8"
    return response


def get(url, session=None, **kwargs):
    client = session or requests
    started = time.monotonic()
    attempts = 0
    status = "error"
    try:
        for attempt in range(3):
            attempts += 1
            try:
                hostname = (urlparse(url).hostname or '').lower()
                if session is None and hostname in {'chathamhouse.org', 'www.chathamhouse.org', 'iiss.org', 'www.iiss.org', 'cnas.org', 'www.cnas.org'}:
                    response = browser_get(url, **kwargs)
                else:
                    response = client.get(url, **kwargs)
                status = response.status_code
                if status not in {429, 500, 502, 503, 504} or attempt == 2:
                    return response
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 2:
                    raise
            time.sleep(min(2 ** attempt, 4))
    finally:
        run = scan_runtime.current()
        if run:
            row = run.network.setdefault(url, {"attempts": 0, "seconds": 0})
            row["attempts"] += attempts
            row["seconds"] = round(row["seconds"] + time.monotonic() - started, 3)
            row["last_status"] = status
