"""V0.11 review & submit, the parts that need no browser and no database: the state hash,
which button is *the* final action, what counts as a confirmation, the single-use permit,
and the structure that keeps submission a separate capability.
"""

import copy
import inspect
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.services.automation import safety
from app.services.automation.browser import base
from app.services.automation.browser.final_submit import (
    FinalSubmissionChannel,
    OutcomeSignals,
    PermitError,
    SubmitControl,
    SubmitPermit,
    issue_permit,
)
from app.services.automation.review import canonical_json, hash_state, text_hash

APP = Path(__file__).resolve().parents[1] / "app"


def _state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "application_id": "a",
        "session_id": "s",
        "destination": {"host": "jobs.example.com", "domain": "example.com", "url": "x"},
        "fields": [
            {
                "field_id": str(uuid.uuid4()),
                "field_key": "field:id:email",
                "label": "Email",
                "value": "me@example.com",
                "question_id": "q1",
                "source": "USER",
            }
        ],
        "documents": [
            {
                "field_id": str(uuid.uuid4()),
                "field_key": "field:id:cv",
                "document_id": "d1",
                "version_number": 2,
                "document_label": "Tailored CV v2",
                "content_hash": "c" * 64,
            }
        ],
        "consents": [
            {
                "field_id": str(uuid.uuid4()),
                "field_key": "field:id:privacy",
                "text": "I agree",
                "text_hash": text_hash("I agree"),
                "state": "1",
                "authorized": True,
                "authorization_id": str(uuid.uuid4()),
                "authorized_at": "2026-10-07T10:00:00+00:00",
            }
        ],
        "submit_control": {"label": "Submit application", "tag": "button", "type": "submit"},
    }


# --- The hash ------------------------------------------------------------------------------------


def test_identical_state_gives_an_identical_hash() -> None:
    state = _state()
    again = copy.deepcopy(state)
    assert hash_state(state) == hash_state(again)
    assert re.fullmatch(r"[0-9a-f]{64}", hash_state(state))
    # Key order does not matter: the JSON is canonical.
    reordered = dict(reversed(list(again.items())))
    assert hash_state(reordered) == hash_state(state)


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["fields"][0].update(value="other@example.com"),
        lambda s: s["fields"][0].update(question_id="q2"),
        lambda s: s["fields"][0].update(source="ACCEPTED_AI"),
        lambda s: s["documents"][0].update(document_id="d2"),
        lambda s: s["documents"][0].update(version_number=3),
        lambda s: s["documents"][0].update(content_hash="d" * 64),
        lambda s: s["consents"][0].update(text="I agree to everything", text_hash="e" * 64),
        lambda s: s["consents"][0].update(state="0"),
        lambda s: s["consents"][0].update(authorized=False),
        lambda s: s["destination"].update(host="evil.example.org"),
        lambda s: s.update(session_id="another-session"),
        lambda s: s.update(application_id="another-application"),
        lambda s: s["submit_control"].update(label="Apply now"),
        lambda s: s["fields"].append({"field_key": "field:id:new", "value": None}),
    ],
)
def test_every_meaningful_change_changes_the_hash(change: Any) -> None:
    state = _state()
    changed = copy.deepcopy(state)
    change(changed)
    assert hash_state(changed) != hash_state(state)


def test_volatile_metadata_does_not_change_the_hash() -> None:
    state = _state()
    volatile = copy.deepcopy(state)
    volatile["fields"][0]["field_id"] = str(uuid.uuid4())
    volatile["documents"][0]["field_id"] = str(uuid.uuid4())
    volatile["documents"][0]["document_label"] = "Tailored CV v2 (renamed label)"
    volatile["consents"][0]["authorization_id"] = str(uuid.uuid4())
    volatile["consents"][0]["authorized_at"] = "2027-01-01T00:00:00+00:00"
    assert hash_state(volatile) == hash_state(state)
    assert "field_id" not in canonical_json(state)
    assert "authorized_at" not in canonical_json(state)


def test_a_consent_text_hash_is_whitespace_stable_but_word_sensitive() -> None:
    assert text_hash("I  agree\nto the terms") == text_hash("I agree to the terms")
    assert text_hash("I agree to the terms") != text_hash("I agree to the new terms")


# --- Which button is the final action ------------------------------------------------------------


def _button(label: str, **kw: Any) -> SubmitControl:
    return SubmitControl(
        label=label, tag=kw.pop("tag", "button"), type=kw.pop("type", "submit"), **kw
    )


@pytest.mark.parametrize(
    "label",
    [
        "Submit application",
        "Send application",
        "Complete application",
        "Apply",
        "Bewerbung absenden",
        "Bewerbung einreichen",
        "Jetzt bewerben",
        "Bewerbung abschicken",
    ],
)
def test_a_single_final_button_is_identified(label: str) -> None:
    verdict = safety.final_controls([_button("Save draft"), _button(label), _button("Back")])
    assert verdict.problem is None and verdict.control is not None
    assert verdict.control.label == label


def test_type_submit_alone_is_not_a_final_action() -> None:
    verdict = safety.final_controls([_button("Next"), _button("Save draft"), _button("Upload")])
    assert verdict.control is None and verdict.problem == "NO_SUBMIT_CONTROL"


def test_two_plausible_final_actions_are_ambiguous_and_blocked() -> None:
    verdict = safety.final_controls([_button("Apply now"), _button("Submit application")])
    assert verdict.control is None and verdict.problem == "MULTIPLE_SUBMIT_CONTROLS"
    assert len(verdict.candidates) == 2


def test_a_link_or_disabled_button_is_never_the_pressed_control() -> None:
    link = safety.final_controls([_button("Apply now", tag="a", type="link")])
    assert link.control is None and link.problem == "SUBMIT_CONTROL_NOT_PRESSABLE"
    disabled = safety.final_controls([_button("Submit application", disabled=True)])
    assert disabled.control is None and disabled.problem == "NO_SUBMIT_CONTROL"


def test_the_fill_assistant_still_never_submits() -> None:
    # V0.11 adds a separate capability; the V0.10 flag is unchanged.
    assert safety.SUBMIT_ACTIONS_ALLOWED is False


# --- What counts as a confirmation ---------------------------------------------------------------


def _page(url: str = "https://jobs.example.com/apply", text: str = "", **kw: Any) -> OutcomeSignals:
    return OutcomeSignals(url=url, title=kw.pop("title", "Apply"), text=text, **kw)


def test_a_confirmation_message_and_reference_are_evidence() -> None:
    after = _page(
        "https://jobs.example.com/apply/done",
        "Thank you for your application. Application ID: ABC-12345",
    )
    found = safety.confirmation_of(after, _page())
    assert found.confirmed and found.reference == "ABC-12345"
    assert found.summary == "thank you for your application"


def test_german_confirmations_are_recognized() -> None:
    found = safety.confirmation_of(
        _page(text="Vielen Dank für Ihre Bewerbung! Ihre Bewerbung wurde erfolgreich übermittelt."),
        _page(),
    )
    assert found.confirmed


def test_a_confirmation_url_or_marker_is_evidence() -> None:
    assert safety.confirmation_of(_page("https://jobs.example.com/thank-you"), _page()).confirmed
    marked = _page(success_marker=True, reference_marker="REF-2026-77")
    found = safety.confirmation_of(marked, _page())
    assert found.confirmed and found.evidence == "MARKER" and found.reference == "REF-2026-77"


def test_a_click_without_evidence_is_not_a_success() -> None:
    before = _page(text="Apply now. Thank you for applying to Example!")
    # The same text that was already on the page before the press proves nothing.
    assert not safety.confirmation_of(before, before).confirmed
    assert not safety.confirmation_of(_page(text="Processing..."), _page()).confirmed
    assert not safety.confirmation_of(_page(text="Please fix the errors below."), _page()).confirmed


def test_only_a_reference_shaped_value_is_kept() -> None:
    found = safety.confirmation_of(
        _page(text="Your application has been submitted. Reference number: <script>"), _page()
    )
    assert found.confirmed and found.reference is None


# --- The permit ----------------------------------------------------------------------------------


def _permit() -> SubmitPermit:
    return issue_permit(
        attempt_id=uuid.uuid4(),
        authorization_id=uuid.uuid4(),
        expected_host="jobs.example.com",
        control=_button("Submit application"),
    )


def test_a_permit_cannot_be_forged() -> None:
    with pytest.raises(PermitError):
        SubmitPermit(
            attempt_id=uuid.uuid4(),
            authorization_id=uuid.uuid4(),
            expected_host="jobs.example.com",
            control=_button("Submit application"),
            issuer=object(),
        )


def test_a_permit_is_single_use() -> None:
    permit = _permit()
    permit.redeem()
    with pytest.raises(PermitError):
        permit.redeem()


def test_only_the_submission_guard_issues_permits() -> None:
    callers = [
        path.relative_to(APP).as_posix()
        for path in APP.rglob("*.py")
        if "issue_permit(" in path.read_text() and path.name != "final_submit.py"
    ]
    assert callers == ["services/automation/submission.py"]


def test_the_only_code_that_presses_is_the_permit_gated_channel() -> None:
    """`.click(` appears in exactly one place of the application: the final-submission
    channel, after a trial press, on the element a permit names."""
    pressing = {
        path.relative_to(APP).as_posix(): path.read_text().count(".click(")
        for path in APP.rglob("*.py")
        if ".click(" in path.read_text()
    }
    assert pressing == {"services/automation/browser/playwright_driver.py": 2}
    source = (APP / "services/automation/browser/playwright_driver.py").read_text()
    channel = source[
        source.index("class PlaywrightFinalSubmission") : source.index("class PlaywrightSession")
    ]
    assert channel.count(".click(") == 2 and "trial=True" in channel


# --- Structure: submission is a separate, narrow capability --------------------------------------


def test_the_fill_interfaces_still_cannot_click_submit_or_press() -> None:
    for cls in (base.BrowserPage, base.BrowserSession, base.BrowserDriver):
        assert not [n for n in dir(cls) if "click" in n or "submit" in n or "press" in n]


def test_the_final_submission_channel_has_one_narrow_action() -> None:
    public = {n for n in dir(FinalSubmissionChannel) if not n.startswith("_")}
    assert public == {
        "find_final_controls",
        "page_marker",
        "read_outcome",
        "submit_final_application",
    }
    for forbidden in ("click", "press", "evaluate", "goto", "navigate", "type", "fill", "key"):
        assert not [n for n in public if forbidden in n]
    signature = inspect.signature(FinalSubmissionChannel.submit_final_application)
    params = [p for p in signature.parameters.values() if p.name != "self"]
    assert [(p.name, p.annotation) for p in params] == [("permit", SubmitPermit)]
