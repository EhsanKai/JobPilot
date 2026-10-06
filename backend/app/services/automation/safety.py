"""Safety rules of the browser assistant (V0.10): which pages it may open, what it
must never touch, and what it must hand back to the user.

    SUBMIT_ACTIONS_ALLOWED = False    V0.10 never submits an application

Everything here is deterministic and testable without a browser.
"""

import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Final
from urllib.parse import urlsplit

from app.services.automation.browser.base import PageSnapshot
from app.services.job_discovery.normalization.text import fold

#: V0.10 never submits an application. The browser interface has no way to press a
#: button at all; this flag is the explicit, documented guard the controller checks
#: before it would act on anything that looks like a final action (and a test pins it).
SUBMIT_ACTIONS_ALLOWED: Final = False

MAX_URL_CHARS = 2000


class UnsafeUrl(ValueError):
    """A URL the assistant must not open. `code` is a stable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- URLs ----------------------------------------------------------------------------------------

_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An IP address literal, including the odd spellings browsers accept
    ("2130706433", "0x7f.1", "127.1")."""
    stripped = host.strip("[]")
    try:
        return ipaddress.ip_address(stripped)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(stripped)))
    except (OSError, ValueError):
        return None


def _address_is_private(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def host_is_private(host: str) -> bool:
    """Whether a host name or address is on the machine or a private network
    (checked on the literal; see `resolves_privately` for names)."""
    lowered = host.lower().rstrip(".")
    if not lowered:
        return True
    if lowered == "localhost" or lowered.endswith(_PRIVATE_SUFFIXES):
        return True
    address = _as_ip(lowered)
    if address is not None:
        return _address_is_private(address)
    return "." not in lowered  # a single-label host name is an intranet name


_DNS_TIMEOUT_SECONDS = 3.0


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return [str(info[4][0]) for info in infos]


def resolves_privately(host: str) -> bool:
    """Whether a public-looking host name resolves to a private address (DNS pointing
    a name at the server's own network). Unresolvable names -- and a resolver that does
    not answer within a few seconds -- are not private: the browser will report them as
    unreachable."""
    if _as_ip(host) is not None:
        return host_is_private(host)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        addresses = executor.submit(_resolve, host).result(timeout=_DNS_TIMEOUT_SECONDS)
    except (TimeoutError, OSError):
        return False
    finally:
        executor.shutdown(wait=False)
    for address in addresses:
        try:
            if _address_is_private(ipaddress.ip_address(address)):
                return True
        except ValueError:
            continue
    return False


def validate_application_url(url: str | None, *, allow_private: bool) -> str:
    """The URL the assistant may open, or `UnsafeUrl`. Only http(s), no embedded
    credentials, and -- unless explicitly allowed (development, tests) -- no
    private or loopback address."""
    if not url or not url.strip():
        raise UnsafeUrl("NO_APPLICATION_URL", "This application has no application URL.")
    candidate = url.strip()
    if len(candidate) > MAX_URL_CHARS:
        raise UnsafeUrl("INVALID_APPLICATION_URL", "The application URL is too long.")
    try:
        parts = urlsplit(candidate)
        hostname = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        raise UnsafeUrl("INVALID_APPLICATION_URL", "The application URL is not valid.") from None
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrl(
            "UNSUPPORTED_URL_SCHEME", "Only web (http / https) application pages can be opened."
        )
    if not hostname:
        raise UnsafeUrl("INVALID_APPLICATION_URL", "The application URL has no host.")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrl(
            "UNSAFE_APPLICATION_URL", "The application URL contains a user name or password."
        )
    if not allow_private and (host_is_private(hostname) or resolves_privately(hostname)):
        raise UnsafeUrl(
            "PRIVATE_APPLICATION_URL",
            "The application URL points to a private or local address, which the assistant "
            "does not open.",
        )
    return candidate


def redact_url(url: str | None) -> str | None:
    """scheme://host[:port]/path -- without user info, query or fragment, which
    may carry tokens. This is the only form of a page's URL that is stored."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not parts.scheme or not hostname:
        return None
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port else host
    return f"{parts.scheme}://{netloc}{parts.path}"[:2000]


# --- Which website is "the application" -----------------------------------------------------------

_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "gov", "ac", "edu"})


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def registrable_domain(host: str) -> str:
    """The site a host belongs to (an approximation without a public-suffix list:
    the last two labels, three for co.uk-style names). IP addresses are their own site."""
    if _as_ip(host) is not None:
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def host_in_scope(host: str | None, allowed_hosts: list[str]) -> bool:
    """Whether the page is on a site the user started the assistant on (or later
    confirmed). A page with no host (about:blank, a transient state) is not off-site."""
    if not host:
        return True
    site = registrable_domain(host.lower())
    return any(registrable_domain(allowed.lower()) == site for allowed in allowed_hosts)


# --- Final submission controls: recognized, never clicked ---------------------------------------

_SUBMIT_LABEL = re.compile(
    r"^(submit|apply|send|finish|finalize|complete|done)\b|"
    r"\b(submit|apply now|apply for|send application|send your application|complete application|"
    r"complete your application|finish application|finalize|confirm and apply|"
    r"bewerben|bewerbung (absenden|abschicken|senden|einreichen|abschlie\w+)|absenden|abschicken|"
    r"einreichen|jetzt bewerben)\b"
)
#: Controls that move through a form without submitting it.
_NEUTRAL_LABEL = re.compile(
    r"^(next|continue|back|previous|save|save draft|weiter|zuruck|speichern|upload|attach|"
    r"choose file|browse)\b"
)


def is_submission_label(label: str) -> bool:
    """Whether a button caption looks like the final action of an application
    (English and German). Used to *recognize* it so the user is told to submit
    manually. Nothing ever presses it."""
    folded = fold(label).strip()
    if not folded or len(folded) > 80 or _NEUTRAL_LABEL.match(folded):
        return False
    return bool(_SUBMIT_LABEL.search(folded))


def submission_detected(snapshot: PageSnapshot) -> bool:
    return any(is_submission_label(label) for label in snapshot.signals.button_labels)


# --- Consent and legal confirmations -------------------------------------------------------------

_CONSENT = re.compile(
    r"\b(agree|agreement|accept|consent|privacy|terms|conditions|gdpr|dsgvo|data protection|"
    r"data processing|data policy|i confirm|i declare|i certify|i acknowledge|i have read|"
    r"declaration|certify|true and (complete|correct|accurate)|truthful|signature|sign here|"
    r"e-?sign|newsletter|job alert|marketing|talent pool|stay in touch|future opportunities|"
    r"contact me|opt[- ]?in|"
    r"einverstanden|zustimm\w*|datenschutz\w*|einwillig\w*|einverstandnis|agb|"
    r"nutzungsbedingung\w*|bestatige\w*|versichere\w*|erklare\w*|richtigkeit|"
    r"unterschrift|unterzeichn\w*|bewerberpool|talentpool)\b"
)


def is_consent_label(*texts: str | None) -> bool:
    """Whether a field is a legal confirmation, consent or declaration. These are
    the user's to give: the assistant never ticks them."""
    folded = fold(" ".join(t for t in texts if t))
    return bool(folded and _CONSENT.search(folded))


# --- File fields: which document does it ask for? ------------------------------------------------

_COVER_LETTER = re.compile(
    r"cover[ _-]?letter|anschreiben|motivation\w*|begleitschreiben|letter of interest|"
    r"bewerbungsschreiben"
)
_CV = re.compile(r"\b(cv|resume|lebenslauf|curriculum)\b|^(cv|resume)$|[_-](cv|resume)\b")


def detect_document_type(*texts: str | None) -> str | None:
    """`tailored_cv` or `cover_letter` when the field clearly asks for one of them,
    else None -- an ambiguous or unrelated field is never guessed."""
    folded = fold(" ".join(t for t in texts if t))
    cover = bool(_COVER_LETTER.search(folded))
    cv = bool(_CV.search(folded))
    if cover == cv:
        return None
    return "cover_letter" if cover else "tailored_cv"


# --- What the page asks of the user --------------------------------------------------------------

#: Challenge markers a user can deal with once and then return (a widget on the form).
#: An interstitial ("checking your browser") gates the whole page and is never waved through.
GATE_MARKERS = frozenset({"interstitial"})


def pending_markers(snapshot: PageSnapshot, acknowledged: list[str]) -> list[str]:
    """Security-challenge markers on the page the user has not dealt with yet."""
    return [
        marker
        for marker in snapshot.signals.challenge_markers
        if marker in GATE_MARKERS or marker not in acknowledged
    ]
