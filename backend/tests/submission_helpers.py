"""Helpers for the V0.11 review & submit tests.

A real headless Chromium drives the fake employer's `/submit-apply` page (a form whose final
button really submits it to the fake server, which then shows a confirmation). The person at
the keyboard is `Human`, as in V0.10.
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.automation_helpers import base, post, session_url
from tests.document_helpers import generate
from tests.question_helpers import ok
from tests.test_automation_fields import Ctx, make_ctx

PRIVACY = (
    "I agree to the processing of my personal data for this application in accordance with "
    "the privacy policy"
)
TALENT_POOL = "Keep my profile in the talent pool for future opportunities"

#: The answers the user gives to the questions of `/submit-apply` that application data
#: does not answer.
ANSWERS: dict[str, str] = {
    "Email address": "jane@example.com",
    "Phone number": "+49 170 1234567",
    "Years of experience": "7",
    "Why do you want to join us?": "Because I like integration work.",
}


class SubmitCtx(Ctx):
    """A running assistant on a page whose final button really submits."""

    def review_url(self) -> str:
        return f"{session_url(self.aid, self.sid)}/final-review"

    def final_review(self) -> dict[str, Any]:
        return ok(self.client.get(self.review_url(), headers=self.auth))  # type: ignore[no-any-return]

    def confirm_review(self, state_hash: str | None = None) -> Any:
        if state_hash is None:
            state_hash = self.final_review()["current"]["state_hash"]
        return self.client.post(
            self.review_url(), headers=self.auth, json={"expected_state_hash": state_hash}
        )

    def consent(self, label: str, *, give: bool = True, text_hash: str | None = None) -> Any:
        consent = self.consent_item(label)
        return self.client.post(
            f"{session_url(self.aid, self.sid)}/consents/{consent['field_id']}",
            headers=self.auth,
            json={"consent_text_hash": text_hash or consent["text_hash"], "give": give},
        )

    def consent_item(self, label: str) -> dict[str, Any]:
        items = [c for c in self.final_review()["current"]["consents"] if c["text"] == label]
        assert len(items) == 1, label
        return items[0]

    def authorize(self, review: dict[str, Any] | None = None, **overrides: Any) -> Any:
        if review is None:
            review = ok(self.confirm_review(), 201)
        body: dict[str, Any] = {
            "final_review_id": review["id"],
            "reviewed_state_hash": review["state_hash"],
            "confirm": True,
        }
        body.update(overrides)
        return self.client.post(
            f"{session_url(self.aid, self.sid)}/submission-authorization",
            headers=self.auth,
            json=body,
        )

    def submit(self, authorization_id: str, **extra: Any) -> Any:
        return self.client.post(
            f"{session_url(self.aid, self.sid)}/submit",
            headers=self.auth,
            json={"authorization_id": authorization_id, **extra},
        )

    def attempts(self) -> dict[str, Any]:
        return ok(self.client.get(f"{base(self.aid)}/submissions", headers=self.auth))  # type: ignore[no-any-return]

    def prepare(self, *, consent: bool = True) -> None:
        """Everything a careful user does before the final review: answer, choose the CV,
        fill, and (optionally) give the required consent."""
        for label, text in ANSWERS.items():
            self.answer(label, text)
        self.answer("Are you legally authorized to work in Germany?", "Yes")
        cv = self.field("Resume / CV")
        choice = cv["document"]["proposed"]
        assert choice is not None
        ok(
            self.client.put(
                f"{session_url(self.aid, self.sid)}/fields/{cv['id']}/document",
                headers=self.auth,
                json={"document_type": choice["type"], "document_id": choice["id"]},
            )
        )
        self.fill()
        if consent:
            response = self.consent(PRIVACY)
            assert response.status_code == 204, response.text

    def ready_authorization(self) -> dict[str, Any]:
        """A prepared application, reviewed and authorized: the authorization."""
        self.prepare()
        review = ok(self.confirm_review(), 201)
        return ok(self.authorize(review), 201)


def make_submit_ctx(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    site: Any,
    *,
    path: str = "/submit-apply",
    email: str = "jane@example.com",
) -> SubmitCtx:
    context = make_ctx(client, auth, db, site, path=path, resume=True)
    assert generate(client, auth, context.aid, "cv").status_code == 201
    # The CV appeared after the session started: let the assistant read the page again.
    ok(post(client, auth, context.aid, context.sid, "discover"))
    return SubmitCtx(client, auth, db, site, context.app, context.session)


def post_action(ctx: Ctx, action: str, body: dict[str, Any] | None = None) -> Any:
    return post(ctx.client, ctx.auth, ctx.aid, ctx.sid, action, body)
