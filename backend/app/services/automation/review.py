"""The submission state (V0.11): exactly what would be submitted, and its SHA-256.

    live page + session fields + answers + documents + consents
        --> SubmissionState      every field with its final value, source and verification,
                                 every document with its pinned version and content hash,
                                 every consent with its exact text, state and authorization,
                                 the destination and the one final button
        --> blockers             why it must not be submitted (yet)
        --> state_hash           SHA-256 of the canonical JSON of the state

The same computation runs three times, and must give the same hash each time:

1. when the user looks at the final review (and confirms it -> `FinalReview`),
2. when they authorize the submission,
3. immediately before the final button is pressed.

So `state at submission == state the user reviewed` is checked, not assumed. The hash
covers everything that decides what is sent (field identities, values, question
associations and mappings, document ids / versions / content hashes, consent texts,
states and authorizations, the destination, the final button) and nothing volatile
(timestamps, row ids that do not change what is sent, screenshots, UI state). Which page
*instance* was reviewed (a reload makes a new one) is compared separately, via
`page_marker`.

Values are read from the page itself (what would really be sent) and compared with what
was approved. Nothing here writes to the page; with `sync=True` it brings the session's
field records up to date first (a V0.10 discovery: reads only).
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.application import Application
from app.models.application_question import ApplicationQuestion
from app.models.automation import (
    AutomationField,
    AutomationSession,
    AutomationStatus,
    FillStatus,
    MappingStatus,
)
from app.models.submission import (
    ConsentAuthorization,
    SubmissionAttempt,
    SubmissionResult,
)
from app.models.user import User
from app.services.automation import documents, filler, messages, safety
from app.services.automation.browser.base import BrowserError, PageSnapshot
from app.services.automation.browser.final_submit import (
    FinalSubmissionChannel,
    SubmitControl,
    SupportsFinalSubmission,
)
from app.services.automation.discovery import DiscoveredField, discover_fields
from app.services.automation.registry import LiveSession
from app.services.automation.resolver import AnswerResolver, AnswerState, ResolvedAnswer
from app.services.job_discovery.normalization.text import fold

SCHEMA_VERSION = 1

#: Keys that identify a row for display but do not change what is submitted: never hashed.
_NOT_HASHED = frozenset({"field_id", "authorization_id", "document_label", "authorized_at"})

_WORKING = (AutomationStatus.RUNNING, AutomationStatus.COMPLETED)
_PERSONAL = re.compile(
    r"\b((first|last|given|family|full|sur|middle)? ?name|e-?mail|phone|telefon|mobile|handy|"
    r"address|adresse|street|strasse|city|stadt|ort|postal|zip|plz|postleitzahl|country|land|"
    r"linkedin|github|website|portfolio|vorname|nachname|geburtsdatum|date of birth)\b"
)

BLOCKER_MESSAGES: dict[str, str] = {
    "SESSION_NOT_READY": "The browser assistant is not running. Resume it to review the page.",
    "USER_IN_CONTROL": (
        "You are in control of the browser. Resume the assistant so it can read the page "
        "for the final review."
    ),
    "SESSION_PAUSED": "The assistant is paused. Resume it to review the page.",
    "BROWSER_GONE": "The browser is no longer running. Start the assistant again.",
    "UNEXPECTED_DOMAIN": (
        "The browser is on a website this application did not start on. Confirm it or take control."
    ),
    "SECURITY_CHALLENGE": (
        "The page shows a security check (such as a CAPTCHA). Only you can deal with it."
    ),
    "LOGIN_REQUIRED": "The page asks you to sign in. Only you can do that.",
    "ACCESS_RESTRICTED": "The website did not allow access to this page.",
    "TOO_MANY_FIELDS": "The page has more fields than JobPilot reads, so it cannot review all.",
    "NO_FORM_FOUND": "No application form was found on this page.",
    "PAGE_CHANGED": (
        "The form changed since the assistant last read it. Rediscover the fields and review again."
    ),
    "NO_SUBMIT_CONTROL": "No final submit button was found on this page.",
    "MULTIPLE_SUBMIT_CONTROLS": (
        "The page has more than one button that could submit the application. JobPilot does "
        "not guess: submit it yourself, or remove the ambiguity first."
    ),
    "SUBMIT_CONTROL_NOT_PRESSABLE": (
        "The final action is not a form button JobPilot may press (a link, say). Submit it "
        "yourself."
    ),
    "FIELD_GONE": "A field is no longer on the page.",
    "MAPPING_NEEDS_REVIEW": "JobPilot is not sure which question this field is. Confirm it.",
    "SUGGESTION_NOT_REVIEWED": (
        "Only an AI suggestion exists. Accept or edit it, or leave the field unmapped."
    ),
    "NEEDS_INPUT": (
        "No approved answer. Answer it, type it in the browser, or leave the field unmapped "
        "to send it empty."
    ),
    "REQUIRED_FIELD_EMPTY": "A required field is empty.",
    "NOT_FILLED_YET": "An approved answer is not in the form yet. Fill the approved answers.",
    "ANSWER_UNFILLABLE": "The approved answer does not fit this field.",
    "ANSWER_CHANGED": (
        "The approved answer changed after it was filled. Fill again so the form shows it."
    ),
    "FILL_FAILED": "Filling this field failed.",
    "UNVERIFIABLE_FIELD": (
        "JobPilot cannot read this kind of field, so it cannot show what would be sent."
    ),
    "DOCUMENT_UNAVAILABLE": "The chosen document is no longer available.",
    "DOCUMENT_NOT_ATTACHED": "The chosen document is not attached yet. Fill the approved answers.",
    "REQUIRED_DOCUMENT_MISSING": "A required file is missing.",
    "CONSENT_NOT_AUTHORIZED": "This consent needs your explicit authorization.",
    "CONSENT_GIVEN_WITHOUT_AUTHORIZATION": (
        "This consent is ticked in the page, but you have not authorized it in JobPilot."
    ),
    "CONSENT_NOT_GIVEN": "You authorized this required consent, but it is not given in the page.",
    "CONSENT_TEXT_CHANGED": (
        "The text of this consent changed since you authorized it. Read it and authorize it again."
    ),
    "ALREADY_SUBMITTED": "This application was already submitted.",
    "PREVIOUS_SUBMISSION_UNKNOWN": (
        "An earlier submission attempt has an unknown result. Check with the employer and "
        "record what you found before anything is submitted again."
    ),
    "SUBMISSION_IN_PROGRESS": "A submission of this application is in progress.",
}

WARNING_MESSAGES: dict[str, str] = {
    "NEWER_DOCUMENT_VERSION": (
        "A newer version of this document exists. The version shown is the one you chose; it "
        "is never replaced by itself."
    ),
    "PAGE_VALUE": "This value was already on the page; JobPilot did not enter it.",
}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    field_id: uuid.UUID | None = None
    label: str | None = None


def _issue(code: str, row: AutomationField | None = None, label: str | None = None) -> Issue:
    return Issue(
        code,
        BLOCKER_MESSAGES.get(code) or WARNING_MESSAGES.get(code, code),
        row.id if row is not None else None,
        label if label is not None else (row.label if row is not None else None),
    )


@dataclass
class SubmissionState:
    """The live submission state. `state` is canonical (what is hashed, minus `_NOT_HASHED`
    keys); the rest is for the decision and the display."""

    state: dict[str, Any]
    state_hash: str
    blockers: list[Issue]
    warnings: list[Issue]
    page_marker: str
    control: SubmitControl | None
    destination_host: str
    destination_domain: str
    destination_url: str
    #: Built from the live page (not just from the session's status).
    observed: bool = False

    @property
    def ready(self) -> bool:
        return not self.blockers


# --- Hashing -------------------------------------------------------------------------------------


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in _NOT_HASHED}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def canonical_json(state: dict[str, Any]) -> str:
    """Deterministic JSON of the hashed part of a state: sorted keys, no whitespace."""
    return json.dumps(_strip(state), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_state(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    """The SHA-256 of a consent text exactly as the page shows it (whitespace normalized)."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


# --- What the page holds -------------------------------------------------------------------------


def channel_of(live: LiveSession) -> FinalSubmissionChannel | None:
    browser = live.browser
    return browser.final_submission() if isinstance(browser, SupportsFinalSubmission) else None


def consent_text_of(item: DiscoveredField) -> str:
    """The exact consent a field asks for: its label, plus the options of a group."""
    text = item.label
    if item.input_type in ("checkbox_group", "radio", "select") and item.options:
        text = f"{text} — " + " / ".join(o["label"] for o in item.options)
    return text


def _value_label(row_options: list[dict[str, str]] | None, input_type: str, canon: str) -> str:
    """What the user sees for a value: an option's label, "Yes"/"No" for a checkbox."""
    if input_type == "checkbox":
        return "Yes (ticked)" if canon == "1" else "No (not ticked)"
    if input_type in ("select", "radio", "checkbox_group") and row_options:
        labels = {o["value"]: o["label"] for o in row_options}
        values = canon.split(",") if input_type == "checkbox_group" else [canon]
        return ", ".join(labels.get(v, v) for v in values if v)
    return canon


def _trusted(row: AutomationField) -> bool:
    return row.mapping_status is MappingStatus.USER_CONFIRMED or (
        row.mapping_status is MappingStatus.AUTO_MAPPED and row.mapping_confidence == "HIGH"
    )


def _matches(row: AutomationField, plan: filler.Intended, canon: str) -> bool:
    if plan.kind == "text" and row.input_type == "number":
        try:
            return float(canon) == float(plan.canon)
        except ValueError:
            return False
    return canon == plan.canon


# --- The state -----------------------------------------------------------------------------------


@dataclass
class _Build:
    db: Session
    user: User
    application: Application
    session: AutomationSession
    live: LiveSession
    resolver: AnswerResolver
    questions: dict[uuid.UUID, ApplicationQuestion]
    blockers: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)


def _session_blockers(session: AutomationSession, live: LiveSession | None) -> list[Issue]:
    if live is None or not live.browser.is_alive() or live.detached:
        return [_issue("BROWSER_GONE")]
    status = session.status
    if status in _WORKING:
        return []
    if status is AutomationStatus.USER_CONTROL:
        return [_issue("USER_IN_CONTROL")]
    if status is AutomationStatus.BLOCKED:
        code = session.attention_code or "SECURITY_CHALLENGE"
        return [_issue(code if code in BLOCKER_MESSAGES else "SECURITY_CHALLENGE")]
    if status is AutomationStatus.PAUSED:
        if session.attention_code == messages.UNEXPECTED_DOMAIN:
            return [_issue("UNEXPECTED_DOMAIN")]
        return [_issue("SESSION_PAUSED")]
    return [_issue("SESSION_NOT_READY")]


def history_blockers(
    db: Session, application_id: uuid.UUID, *, ignore: uuid.UUID | None = None
) -> list[Issue]:
    """An application that was submitted -- or may have been -- is not submitted again."""
    attempts = db.scalars(
        select(SubmissionAttempt)
        .where(SubmissionAttempt.application_id == application_id)
        .execution_options(populate_existing=True)
    )
    found: list[Issue] = []
    for attempt in attempts:
        if attempt.id == ignore:
            continue
        if attempt.finished_at is None:
            found.append(_issue("SUBMISSION_IN_PROGRESS"))
        elif attempt.result is SubmissionResult.SUCCESS or (
            attempt.user_verified_outcome == "SUBMITTED"
        ):
            found.append(_issue("ALREADY_SUBMITTED"))
        elif (
            attempt.result is SubmissionResult.SUBMISSION_UNKNOWN
            and attempt.user_verified_outcome is None
        ):
            found.append(_issue("PREVIOUS_SUBMISSION_UNKNOWN"))
    unique: dict[str, Issue] = {i.code: i for i in found}
    return list(unique.values())


def _empty_state(
    application: Application, session: AutomationSession, blockers: list[Issue]
) -> SubmissionState:
    host = safety.host_of(session.current_url) or ""
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "application_id": str(application.id),
        "session_id": str(session.id),
        "destination": {
            "host": host,
            "domain": safety.registrable_domain(host) if host else "",
            "url": session.current_url or "",
        },
        "fields": [],
        "documents": [],
        "consents": [],
        "submit_control": None,
    }
    return SubmissionState(
        state=state,
        state_hash=hash_state(state),
        blockers=blockers,
        warnings=[],
        page_marker="",
        control=None,
        destination_host=host,
        destination_domain=state["destination"]["domain"],
        destination_url=state["destination"]["url"],
    )


def compute(
    db: Session,
    user: User,
    application: Application,
    session: AutomationSession,
    live: LiveSession | None,
    *,
    sync: bool,
    ignore_attempt: uuid.UUID | None = None,
) -> SubmissionState:
    """The live submission state of a session. The caller holds `live.lock`.

    With `sync` the session's fields are brought up to date first (a discovery: reads the
    page, writes only JobPilot's own records, commits). Without it nothing is written --
    used under the submission gate, where a commit would release the lock."""
    blockers = _session_blockers(session, live)
    blockers += history_blockers(db, application.id, ignore=ignore_attempt)
    if live is None or any(
        b.code in ("BROWSER_GONE", "USER_IN_CONTROL", "SESSION_PAUSED", "SESSION_NOT_READY")
        or b.code in ("SECURITY_CHALLENGE", "LOGIN_REQUIRED", "ACCESS_RESTRICTED")
        or b.code == "UNEXPECTED_DOMAIN"
        for b in blockers
    ):
        return _empty_state(application, session, blockers)
    channel = channel_of(live)
    if channel is None:  # pragma: no cover - every real browser offers it
        return _empty_state(application, session, [*blockers, _issue("SESSION_NOT_READY")])

    if sync:
        from app.services.automation import controller

        inspection = controller._inspect(db, user, application, session, live, None)  # noqa: SLF001
        if inspection.outcome != "running":
            db.refresh(session)
            return _empty_state(
                application,
                session,
                [*_session_blockers(session, live), *blockers],
            )
        controller._reconcile(db, session, live, inspection.fields)  # noqa: SLF001
        db.commit()

    try:
        snapshot = live.browser.page.snapshot(max_controls=settings.AUTOMATION_MAX_FIELDS)
        marker = channel.page_marker()
        verdict = safety.final_controls(channel.find_final_controls())
    except BrowserError:
        return _empty_state(application, session, [*blockers, _issue("BROWSER_GONE")])
    return _build(db, user, application, session, live, snapshot, marker, verdict, blockers)


def _questions(db: Session, application: Application) -> dict[uuid.UUID, ApplicationQuestion]:
    return {
        q.id: q
        for q in db.scalars(
            select(ApplicationQuestion)
            .where(ApplicationQuestion.application_id == application.id)
            .options(selectinload(ApplicationQuestion.answer))
            .execution_options(populate_existing=True)
        )
    }


def _build(
    db: Session,
    user: User,
    application: Application,
    session: AutomationSession,
    live: LiveSession,
    snapshot: PageSnapshot,
    marker: str,
    verdict: safety.FinalControlVerdict,
    blockers: list[Issue],
) -> SubmissionState:
    host = safety.host_of(snapshot.url) or ""
    context = _Build(
        db,
        user,
        application,
        session,
        live,
        AnswerResolver(db, user, application),
        _questions(db, application),
        blockers=list(blockers),
    )
    if not safety.host_in_scope(host, list(session.allowed_hosts)):
        context.blockers.append(_issue("UNEXPECTED_DOMAIN"))
    if safety.pending_markers(snapshot, list(session.acknowledged_markers)):
        context.blockers.append(_issue("SECURITY_CHALLENGE"))
    if snapshot.signals.has_password:
        context.blockers.append(_issue("LOGIN_REQUIRED"))
    if snapshot.truncated:
        context.blockers.append(_issue("TOO_MANY_FIELDS"))

    rows = {
        row.field_key: row
        for row in db.scalars(
            select(AutomationField)
            .where(AutomationField.session_id == session.id)
            .execution_options(populate_existing=True)
        )
    }
    discovered = discover_fields(snapshot)
    if not discovered:
        context.blockers.append(_issue("NO_FORM_FOUND"))
    known = {key for key, row in rows.items() if row.present}
    live_keys = {item.field_key for item in discovered}
    if live_keys != known:
        context.blockers.append(_issue("PAGE_CHANGED"))

    fields: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    consents: list[dict[str, Any]] = []
    for item in discovered:
        row = rows.get(item.field_key)
        if row is None:
            continue  # reported as PAGE_CHANGED above
        try:
            value, canon = filler.read_current(live.browser.page, row)
        except BrowserError:
            context.blockers.append(_issue("FIELD_GONE", row))
            continue
        if not value.found:
            context.blockers.append(_issue("FIELD_GONE", row))
            continue
        if item.user_action_reason == "LEGAL_CONSENT":
            consents.append(_consent(context, row, item, canon))
        elif item.input_type == "file":
            docs.append(_document(context, row, item, canon))
        else:
            fields.append(_field(context, row, item, canon))

    if verdict.problem is not None:
        context.blockers.append(_issue(verdict.problem))
    destination_url = safety.redact_url(snapshot.url) or ""
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "application_id": str(application.id),
        "session_id": str(session.id),
        "destination": {
            "host": host,
            "domain": safety.registrable_domain(host) if host else "",
            "url": destination_url,
        },
        "fields": sorted(fields, key=lambda f: f["field_key"]),
        "documents": sorted(docs, key=lambda d: d["field_key"]),
        "consents": sorted(consents, key=lambda c: c["field_key"]),
        "submit_control": verdict.control.as_dict() if verdict.control is not None else None,
    }
    return SubmissionState(
        state=state,
        state_hash=hash_state(state),
        blockers=_dedupe(context.blockers),
        warnings=_dedupe(context.warnings),
        page_marker=marker,
        control=verdict.control,
        destination_host=host,
        destination_domain=state["destination"]["domain"],
        destination_url=destination_url,
        observed=True,
    )


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: dict[tuple[str, uuid.UUID | None], Issue] = {}
    for issue in issues:
        seen.setdefault((issue.code, issue.field_id), issue)
    return list(seen.values())


def _field(c: _Build, row: AutomationField, item: DiscoveredField, canon: str) -> dict[str, Any]:
    """One answered (or empty) form field: its final value, where it came from, and
    whether the page really holds the approved value."""
    question = c.questions.get(row.question_id) if row.question_id else None
    required = bool(item.required or (question is not None and question.required))
    empty = filler.is_empty_canon(row.input_type, canon)
    entry: dict[str, Any] = {
        "field_id": str(row.id),
        "field_key": row.field_key,
        "label": item.label,
        "input_type": row.input_type,
        "required": required,
        "group": "personal" if _PERSONAL.search(fold(item.label)) else "questions",
        "mapping": row.mapping_status.value,
        "question_id": str(question.id) if question is not None else None,
        "question": question.question_text if question is not None else None,
        "value": None if empty else canon,
        "value_label": None if empty else _value_label(item.options, row.input_type, canon),
        "approved": None,
        "source": None,
        "approval": "NONE",
        "verification": "EMPTY" if empty else "PAGE_VALUE",
    }

    if item.user_action_reason in ("CUSTOM_CONTROL", "UNSUPPORTED_TYPE"):
        c.blockers.append(_issue("UNVERIFIABLE_FIELD", row, item.label))
        entry.update(approval="UNVERIFIABLE", verification="UNVERIFIABLE", value=None)
        return entry
    if item.user_action_reason == "READ_ONLY":
        entry.update(source="BROWSER_DISCOVERED", approval="READ_ONLY")
        return entry
    if row.mapping_status is MappingStatus.NEEDS_REVIEW:
        c.blockers.append(_issue("MAPPING_NEEDS_REVIEW", row, item.label))
    if row.fill_status is FillStatus.FILL_FAILED:
        c.blockers.append(_issue("FILL_FAILED", row, item.label))

    resolved: ResolvedAnswer | None = None
    if question is not None and _trusted(row):
        resolved = c.resolver.resolve(question)

    written = row.last_agent_value_hash is not None
    still_ours = written and filler.hash_value(row.id, canon) == row.last_agent_value_hash
    if not empty and still_ours and row.fill_status is FillStatus.FILLED:
        plan = filler.plan_value(row, resolved) if resolved and resolved.approved else None
        if isinstance(plan, filler.Intended) and _matches(row, plan, canon):
            assert resolved is not None
            entry.update(
                approved=plan.canon,
                source=resolved.source,
                approval="APPROVED",
                verification="VERIFIED",
            )
        else:
            c.blockers.append(_issue("ANSWER_CHANGED", row, item.label))
            entry.update(source=row.last_value_source, approval="STALE", verification="STALE")
        return entry

    if not empty:
        # Not what JobPilot wrote: the user's own (typed in the browser) -- or a value the
        # website put there itself. Either way it is what would be sent, and it is shown.
        user_typed = written or row.fill_status is FillStatus.USER_MODIFIED
        entry.update(
            source="USER" if user_typed else "BROWSER_DISCOVERED",
            approval="USER_ENTERED" if user_typed else "PAGE_VALUE",
            verification="USER_ENTERED" if user_typed else "PAGE_VALUE",
        )
        if not user_typed:
            c.warnings.append(_issue("PAGE_VALUE", row, item.label))
        return entry

    # Empty.
    if resolved is not None:
        if resolved.state is AnswerState.APPROVED:
            plan = filler.plan_value(row, resolved)
            code = "ANSWER_UNFILLABLE" if isinstance(plan, filler.Unfillable) else "NOT_FILLED_YET"
            c.blockers.append(_issue(code, row, item.label))
            entry["approval"] = "APPROVED_NOT_FILLED"
        elif resolved.state is AnswerState.SUGGESTION_AVAILABLE:
            c.blockers.append(_issue("SUGGESTION_NOT_REVIEWED", row, item.label))
            entry["approval"] = "SUGGESTION_ONLY"
        else:
            c.blockers.append(_issue("NEEDS_INPUT", row, item.label))
            entry["approval"] = "NEEDS_INPUT"
        return entry
    if required:
        c.blockers.append(_issue("REQUIRED_FIELD_EMPTY", row, item.label))
    elif row.mapping_status is not MappingStatus.UNMAPPED and question is None:
        c.blockers.append(_issue("NEEDS_INPUT", row, item.label))
    entry["approval"] = "LEFT_EMPTY"
    return entry


def _document(c: _Build, row: AutomationField, item: DiscoveredField, canon: str) -> dict[str, Any]:
    """One file field: which exact document version is attached, verified by read-back."""
    empty = canon == ""
    entry: dict[str, Any] = {
        "field_id": str(row.id),
        "field_key": row.field_key,
        "label": item.label,
        "required": item.required,
        "file": None if empty else canon,
        "document_type": None,
        "document_id": None,
        "document_label": None,
        "version_number": None,
        "origin": None,
        "content_hash": None,
        "source": None,
        "verification": "EMPTY" if empty else "PAGE_VALUE",
    }
    still_ours = (
        not empty
        and row.last_agent_value_hash is not None
        and filler.hash_value(row.id, canon) == row.last_agent_value_hash
        and row.fill_status is FillStatus.FILLED
    )
    if row.document_confirmed and row.document_type and row.document_id:
        choice = documents.find_choice(
            c.db, c.user, c.application, row.document_type, row.document_id
        )
        if choice is None:
            c.blockers.append(_issue("DOCUMENT_UNAVAILABLE", row, item.label))
            return entry
        if still_ours:
            entry.update(
                document_type=choice.type,
                document_id=str(choice.id),
                document_label=choice.label,
                version_number=choice.version_number,
                origin=choice.origin,
                content_hash=documents.content_identity(c.db, c.user, c.application, choice),
                source="DOCUMENT",
                verification="VERIFIED",
            )
            if choice.type != "resume_file" and not choice.is_current:
                c.warnings.append(_issue("NEWER_DOCUMENT_VERSION", row, item.label))
            return entry
        if empty:
            c.blockers.append(_issue("DOCUMENT_NOT_ATTACHED", row, item.label))
            return entry
    if empty:
        if item.required:
            c.blockers.append(_issue("REQUIRED_DOCUMENT_MISSING", row, item.label))
        return entry
    # A file the user attached in the browser themselves: theirs, shown by name and size.
    entry.update(source="USER", verification="USER_ENTERED")
    return entry


def _live_consent(db: Session, row: AutomationField) -> ConsentAuthorization | None:
    return db.scalar(
        select(ConsentAuthorization)
        .where(
            ConsentAuthorization.field_id == row.id,
            ConsentAuthorization.revoked_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )


def _consent(c: _Build, row: AutomationField, item: DiscoveredField, canon: str) -> dict[str, Any]:
    """One legal confirmation / consent: its exact text, whether the page shows it given,
    and whether the user explicitly authorized exactly that text."""
    text = consent_text_of(item)
    digest = text_hash(text)
    given = not filler.is_empty_canon(row.input_type, canon)
    authorization = _live_consent(c.db, row)
    authorized = authorization is not None and authorization.consent_text_hash == digest
    entry: dict[str, Any] = {
        "field_id": str(row.id),
        "field_key": row.field_key,
        "input_type": row.input_type,
        "text": text,
        "text_hash": digest,
        "required": item.required,
        "state": canon,
        "given": given,
        "authorized": authorized,
        "authorization_id": str(authorization.id) if authorized and authorization else None,
        "authorized_at": (
            authorization.created_at.isoformat() if authorized and authorization else None
        ),
    }
    if authorization is not None and not authorized:
        c.blockers.append(_issue("CONSENT_TEXT_CHANGED", row, item.label))
    elif item.required and not authorized:
        c.blockers.append(_issue("CONSENT_NOT_AUTHORIZED", row, item.label))
    elif given and not authorized:
        c.blockers.append(_issue("CONSENT_GIVEN_WITHOUT_AUTHORIZATION", row, item.label))
    elif item.required and not given:
        c.blockers.append(_issue("CONSENT_NOT_GIVEN", row, item.label))
    return entry


__all__ = [
    "BLOCKER_MESSAGES",
    "SCHEMA_VERSION",
    "Issue",
    "SubmissionState",
    "canonical_json",
    "channel_of",
    "compute",
    "consent_text_of",
    "hash_state",
    "history_blockers",
    "text_hash",
]
