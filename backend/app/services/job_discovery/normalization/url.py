"""URL normalization.

Two deliberately different outputs:

- `canonicalize_url` -- the stored, clickable URL of a posting. Only changes
  that can't alter the destination: lowercase scheme/host, default port,
  fragment, known tracking parameters. The scheme is kept (an http-only
  site must stay reachable).
- `url_identity_key` -- for deduplication only, never opened. Additionally
  treats http and https, and a trailing slash, as the same posting.

Only parameters known to be tracking-only are removed; every other query
parameter is kept byte-for-byte and in order (e.g. Greenhouse's `gh_jid`
selects the posting on company career pages).
"""

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_URL_CHARS = 2000

TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = frozenset(
    {"gclid", "fbclid", "msclkid", "dclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "igshid"}
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _valid_http_url_parts(value: object) -> tuple[str, Any] | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_URL_CHARS or any(c.isspace() for c in value):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        # Credentials in a job URL are never legitimate; don't store them.
        return None
    return value, (scheme, parts, port)


def is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PARAM_PREFIXES)


def normalize_source_url(value: object) -> str | None:
    """The URL as the source gave it, if it is a safe absolute http(s) URL.
    Anything else (javascript:, data:, relative, credentials) -> None."""
    checked = _valid_http_url_parts(value)
    return checked[0] if checked else None


def canonicalize_url(value: object) -> str | None:
    """A stable, still-clickable form of a posting URL (see module docs)."""
    checked = _valid_http_url_parts(value)
    if checked is None:
        return None
    _, (scheme, parts, port) = checked
    host = parts.hostname.lower().rstrip(".")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"

    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        kept = [(k, v) for k, v in pairs if not is_tracking_param(k)]
        if len(kept) != len(pairs):  # only re-encode when something was removed
            query = urlencode(kept)
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


def url_identity_key(value: object) -> str | None:
    """Comparison key: two URLs with the same key lead to the same posting.

    On top of `canonicalize_url`: http and https are equivalent, and a
    trailing slash on the path is ignored. Ports other than the default stay
    significant; the query string (minus tracking parameters) stays
    significant.
    """
    canonical = canonicalize_url(value)
    if canonical is None:
        return None
    # `canonicalize_url` already dropped the scheme's default port.
    parts = urlsplit(canonical)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", parts.netloc, path, parts.query, ""))
