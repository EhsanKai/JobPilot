"""API schemas of review & submit (V0.11).

Requests forbid unknown keys: a client can never send a URL, a selector, a button, form
values, an owner or a status. Submitting takes an authorization id and nothing else -- the
destination, the button and the values are the ones the user reviewed, derived by the
server. Responses carry what the user reviews (their own values, sources, documents,
consent texts) and safe metadata about attempts; never cookies, tokens or page content.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.submission import (
    FinalReviewStatus,
    SubmissionAuthorizationStatus,
    SubmissionResult,
)

Sha256 = Field(pattern=r"^[0-9a-f]{64}$")


# --- Requests ------------------------------------------------------------------------------------


class CreateReviewRequest(BaseModel):
    """Confirm the final review the user is looking at: `expected_state_hash` is the hash
    they were shown. If the application changed meanwhile, nothing is confirmed."""

    model_config = ConfigDict(extra="forbid")

    expected_state_hash: str = Sha256


class ConsentRequest(BaseModel):
    """Authorize one consent -- exactly the text shown (`consent_text_hash`). With
    `give`, JobPilot ticks that one checkbox for you."""

    model_config = ConfigDict(extra="forbid")

    consent_text_hash: str = Sha256
    give: bool = True


class AuthorizationRequest(BaseModel):
    """The explicit authorization. `confirm` must be literally true: "I have reviewed the
    information and authorize JobPilot to submit this application"."""

    model_config = ConfigDict(extra="forbid")

    final_review_id: uuid.UUID
    reviewed_state_hash: str = Sha256
    confirm: Literal[True]


class SubmitRequest(BaseModel):
    """Submit under an authorization. Nothing else is accepted: no URL, no selector, no
    form data."""

    model_config = ConfigDict(extra="forbid")

    authorization_id: uuid.UUID


class VerifyRequest(BaseModel):
    """After an unknown result: what the user found when they checked themselves."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["SUBMITTED", "NOT_SUBMITTED"]


# --- Responses ------------------------------------------------------------------------------------


class IssueRead(BaseModel):
    code: str
    message: str
    field_id: uuid.UUID | None = None
    label: str | None = None


class DestinationRead(BaseModel):
    host: str
    domain: str
    url: str
    company: str | None = None
    position: str | None = None
    source: str | None = None


class ReviewFieldRead(BaseModel):
    field_id: uuid.UUID | None
    field_key: str
    label: str
    input_type: str
    required: bool
    #: "personal" (name, contact ...) or "questions".
    group: str
    mapping: str
    question_id: uuid.UUID | None
    question: str | None
    #: The final value, as the form holds it (None: empty).
    value: str | None
    #: The value as the user sees it (an option's label, "Yes (ticked)" ...).
    value_label: str | None
    #: USER, DETERMINISTIC, ACCEPTED_AI, DOCUMENT, BROWSER_DISCOVERED or None (empty).
    source: str | None
    approval: str
    verification: str


class ReviewDocumentRead(BaseModel):
    field_id: uuid.UUID | None
    field_key: str
    label: str
    required: bool
    #: "name:size" of the attached file, as the page reports it.
    file: str | None
    document_type: str | None
    document_id: uuid.UUID | None
    document_label: str | None
    version_number: int | None
    origin: str | None
    content_hash: str | None
    source: str | None
    verification: str


class ReviewConsentRead(BaseModel):
    field_id: uuid.UUID | None
    field_key: str
    input_type: str
    #: The exact consent text the page shows.
    text: str
    text_hash: str
    required: bool
    #: Whether the page shows the consent as given (ticked, chosen ...).
    given: bool
    #: Whether the user explicitly authorized exactly this text.
    authorized: bool
    authorized_at: datetime | None = None


class SubmitControlRead(BaseModel):
    label: str


class ReviewStateRead(BaseModel):
    """The live state: exactly what would be submitted now."""

    schema_version: int
    state_hash: str
    #: Read from the live page (False: the assistant could not read it; see blockers).
    observed: bool
    destination: DestinationRead
    fields: list[ReviewFieldRead]
    documents: list[ReviewDocumentRead]
    consents: list[ReviewConsentRead]
    submit_control: SubmitControlRead | None
    blockers: list[IssueRead]
    warnings: list[IssueRead]
    ready: bool


class FinalReviewRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    status: FinalReviewStatus
    state_hash: str
    destination_host: str
    destination_domain: str
    destination_url: str
    reviewed_at: datetime
    invalidated_at: datetime | None
    invalidation_reason: str | None


class AuthorizationRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    final_review_id: uuid.UUID
    reviewed_state_hash: str
    destination_host: str
    status: SubmissionAuthorizationStatus
    authorized_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    invalidated_at: datetime | None
    invalidation_reason: str | None


class FinalReviewResponse(BaseModel):
    current: ReviewStateRead
    #: The latest final review of the session (current or not).
    review: FinalReviewRead | None
    #: The review is ACTIVE and the live state still has its hash.
    review_is_current: bool
    authorization: AuthorizationRead | None
    can_confirm_review: bool
    can_authorize: bool


class AttemptRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    authorization_id: uuid.UUID
    final_review_id: uuid.UUID
    reviewed_state_hash: str
    destination_domain: str
    destination_url: str
    started_at: datetime
    submit_triggered_at: datetime | None
    finished_at: datetime | None
    #: None while the attempt runs.
    result: SubmissionResult | None
    #: In the user's words.
    message: str
    confirmation_reference: str | None
    confirmation_summary: str | None
    failure_code: str | None
    failure_reason: str | None
    user_verified_outcome: str | None
    user_verified_at: datetime | None
    company: str | None = None
    position: str | None = None


class AttemptListResponse(BaseModel):
    items: list[AttemptRead]
    #: Whether the application counts as submitted (a confirmed or user-verified attempt).
    submitted: bool
    #: An attempt with an unknown result waits for the user's check.
    needs_verification: bool
