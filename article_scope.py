"""Publisher-specific article boundaries, preserving unmodified source evidence."""
import re
from urllib.parse import urlparse

VERSION = "atlantic-author-tail-1"
# Atlantic Council places the current author's biography before recommendations.
# Require a named person, an institutional role, and the institutional name.
BIOGRAPHY = re.compile(
    r"(?m)^[A-Z][A-Za-z'’.-]+(?: [A-Z][A-Za-z'’.-]+){1,3}"
    r"(?:\s*,\s*PhD\s*,?)?\s+is\s+(?:a |an |the )?"
    r"(?:nonresident|senior|assistant|director|member|resident|research|fellow|professor)"
    r"[^\n]{0,500}(?:Atlantic Council|Center for International Private Enterprise)")


def trim(text, url):
    text = str(text or "")
    if urlparse(str(url or "")).hostname not in ("atlanticcouncil.org", "www.atlanticcouncil.org"):
        return text, None
    for match in BIOGRAPHY.finditer(text):
        # Do not turn an already thin extraction into a misleading clean body.
        body = text[:match.start()].rstrip()
        if len(body) >= 600 and len(body) >= len(text) * 0.60:
            return body, {"version": VERSION, "reason": "publisher_author_biography_boundary",
                          "original_chars": len(text), "body_chars": len(body),
                          "removed_chars": len(text) - len(body)}
    return text, None


def scoped_item(item):
    body, audit = trim(item.get("extracted_text", ""), item.get("canonical_url") or item.get("url"))
    result = dict(item, extracted_text=body, extracted_text_chars=len(body))
    if audit:
        result["article_scope"] = audit
    return result
