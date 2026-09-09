"""Bounded retries and request metrics for public source requests."""
import time
import requests
import scan_runtime


def get(url, session=None, **kwargs):
    client = session or requests
    started = time.monotonic()
    attempts = 0
    status = "error"
    try:
        for attempt in range(3):
            attempts += 1
            try:
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
