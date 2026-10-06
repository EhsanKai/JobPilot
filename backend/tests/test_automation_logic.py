"""V0.10 rules that need no browser: URL and domain safety, what looks like a final action
or a legal confirmation, field identity, mapping, and what may be written into a control.
"""

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import MappingStatus
from app.models.application_question import (
    AnswerSource,
    AnswerStatus,
    ApplicationQuestion,
    ApplicationQuestionAnswer,
    QuestionType,
)
from app.services.automation import filler, mapping, prompts, safety
from app.services.automation.browser.base import (
    FieldValue,
    PageSignals,
    PageSnapshot,
    RawControl,
    RawOption,
)
from app.services.automation.discovery import (
    discover_fields,
    question_options_of,
    question_type_of,
)
from app.services.automation.resolver import ResolvedAnswer
from app.services.questions.text import normalize_question, question_hash


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public host names resolve to a public address, without touching the network."""
    monkeypatch.setattr(
        safety.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )


# --- Safety: URLs ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "app.localhost",
        "printer.local",
        "db.internal",
        "intranet",
        "127.0.0.1",
        "127.1",
        "2130706433",
        "0x7f.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "[::1]",
        "fe80::1",
        "::ffff:127.0.0.1",
        "",
    ],
)
def test_private_hosts_are_recognized(host: str) -> None:
    assert safety.host_is_private(host)


@pytest.mark.parametrize(
    "host", ["example.com", "jobs.lever.co", "job-boards.greenhouse.io", "8.8.8.8"]
)
def test_public_hosts_are_not_private(host: str) -> None:
    assert not safety.host_is_private(host)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        (None, "NO_APPLICATION_URL"),
        ("   ", "NO_APPLICATION_URL"),
        ("file:///etc/passwd", "UNSUPPORTED_URL_SCHEME"),
        ("javascript:alert(1)", "UNSUPPORTED_URL_SCHEME"),
        ("data:text/html,<h1>x</h1>", "UNSUPPORTED_URL_SCHEME"),
        ("https://", "INVALID_APPLICATION_URL"),
        ("https://a:b@example.com/", "UNSAFE_APPLICATION_URL"),
        ("https://example.com:notaport/", "INVALID_APPLICATION_URL"),
        ("http://127.0.0.1/", "PRIVATE_APPLICATION_URL"),
        ("https://x.com/" + "a" * 2100, "INVALID_APPLICATION_URL"),
    ],
)
def test_unsafe_urls_are_refused(url: str | None, code: str) -> None:
    with pytest.raises(safety.UnsafeUrl) as refused:
        safety.validate_application_url(url, allow_private=False)
    assert refused.value.code == code


def test_a_public_url_is_accepted_and_private_ones_only_when_allowed() -> None:
    assert safety.validate_application_url("https://example.com/apply?x=1", allow_private=False)
    assert safety.validate_application_url("http://127.0.0.1:8000/apply", allow_private=True)


def test_a_name_that_resolves_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        safety.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.7", 0))]
    )
    with pytest.raises(safety.UnsafeUrl) as refused:
        safety.validate_application_url("https://careers.example.com/apply", allow_private=False)
    assert refused.value.code == "PRIVATE_APPLICATION_URL"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.com/apply?token=abc#frag", "https://x.com/apply"),
        ("https://user:pw@x.com:8443/a/b?c=d", "https://x.com:8443/a/b"),
        ("http://[::1]:3000/x?y", "http://[::1]:3000/x"),
        ("not a url", None),
        (None, None),
    ],
)
def test_stored_urls_lose_query_fragment_and_credentials(
    url: str | None, expected: str | None
) -> None:
    assert safety.redact_url(url) == expected


# --- Safety: which website -----------------------------------------------------------------------


def test_the_application_site_is_the_registrable_domain() -> None:
    assert safety.registrable_domain("jobs.example.com") == "example.com"
    assert safety.registrable_domain("a.b.example.co.uk") == "example.co.uk"
    assert safety.registrable_domain("127.0.0.1") == "127.0.0.1"
    allowed = ["careers.example.com"]
    assert safety.host_in_scope("apply.example.com", allowed)
    assert safety.host_in_scope("careers.example.com", allowed)
    assert not safety.host_in_scope("example.org", allowed)
    assert not safety.host_in_scope("evil-example.com", allowed)
    assert not safety.host_in_scope("example.com.evil.net", allowed)
    assert safety.host_in_scope(None, allowed)  # about:blank is not "another website"
    assert not safety.host_in_scope("localhost", ["127.0.0.1"])


# --- Safety: final actions and legal confirmations -----------------------------------------------


@pytest.mark.parametrize(
    "label",
    [
        "Submit",
        "Submit application",
        "Submit your application",
        "Apply",
        "Apply now",
        "Apply for this job",
        "Send application",
        "Complete application",
        "Finish",
        "Finish application",
        "Bewerben",
        "Jetzt bewerben",
        "Bewerbung absenden",
        "Bewerbung abschicken",
        "Absenden",
        "Review and submit",
    ],
)
def test_final_actions_are_recognized_in_english_and_german(label: str) -> None:
    assert safety.is_submission_label(label)


@pytest.mark.parametrize(
    "label",
    [
        "Next",
        "Continue",
        "Back",
        "Save draft",
        "Weiter",
        "Zurück",
        "Speichern",
        "Upload file",
        "",
        "Cancel",
        "x" * 90,
    ],
)
def test_ordinary_buttons_are_not_final_actions(label: str) -> None:
    assert not safety.is_submission_label(label)


@pytest.mark.parametrize(
    "label",
    [
        "I agree to the privacy policy",
        "I accept the terms and conditions",
        "I consent to the processing of my data",
        "I certify that the information above is true and complete",
        "I have read the data protection notice",
        "I declare that my answers are correct",
        "Subscribe to our newsletter",
        "Keep me in the talent pool",
        "Ich bin mit den AGB einverstanden",
        "Ich stimme der Datenschutzerklärung zu",
        "Hiermit versichere ich die Richtigkeit meiner Angaben",
        "Einwilligung zur Datenverarbeitung",
    ],
)
def test_legal_confirmations_are_recognized(label: str) -> None:
    assert safety.is_consent_label(label)


@pytest.mark.parametrize(
    "label",
    [
        "Open to relocation",
        "Remote work",
        "Current employer",
        "Phone number",
        "I have a driving licence",
    ],
)
def test_ordinary_fields_are_not_consents(label: str) -> None:
    assert not safety.is_consent_label(label)


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (("Resume / CV",), "tailored_cv"),
        (("Lebenslauf",), "tailored_cv"),
        (("Upload your résumé", "resume"), "tailored_cv"),
        (("Cover letter",), "cover_letter"),
        (("Anschreiben",), "cover_letter"),
        (("Motivationsschreiben",), "cover_letter"),
        (("Resume and cover letter",), None),  # both: ambiguous, never guessed
        (("Other documents",), None),
        (("Attachments", "attachments"), None),
        (("Portfolio",), None),
    ],
)
def test_the_document_a_file_field_asks_for_is_never_guessed(
    texts: tuple[str, ...], expected: str | None
) -> None:
    assert safety.detect_document_type(*texts) == expected


def test_an_interstitial_is_never_acknowledged_away() -> None:
    snapshot = PageSnapshot(
        "https://x.com", "t", [], PageSignals(challenge_markers=["recaptcha", "interstitial"])
    )
    assert safety.pending_markers(snapshot, []) == ["recaptcha", "interstitial"]
    assert safety.pending_markers(snapshot, ["recaptcha", "interstitial"]) == ["interstitial"]


def test_submitting_is_not_allowed_in_v0_10() -> None:
    assert safety.SUBMIT_ACTIONS_ALLOWED is False


# --- Discovery: identity -------------------------------------------------------------------------


def control(kind: str = "text", label: str = "Field", **kw: Any) -> RawControl:
    return RawControl(kind=kind, label=label, **kw)


def snapshot_of(*controls: RawControl) -> PageSnapshot:
    return PageSnapshot("https://x.com/apply", "Apply", list(controls), PageSignals())


def keys_of(*controls: RawControl) -> list[str]:
    return [f.field_key for f in discover_fields(snapshot_of(*controls))]


def test_identity_prefers_a_stable_id_then_the_name_then_the_label() -> None:
    assert keys_of(
        control(dom_id="candidate-email", name="email"),
        control(name="phone"),
        control(label="Years of experience"),
    ) == ["field:id:candidate-email", "field:name:phone", "field:label:years-of-experience:text"]


def test_generated_ids_are_not_identity() -> None:
    fields = discover_fields(
        snapshot_of(control(dom_id=":r1:", id_stable=False, name="q_17", label="Email"))
    )
    assert fields[0].field_key == "field:name:q_17" and fields[0].dom_id is None
    assert fields[0].locator.dom_id is None


def test_a_label_change_does_not_change_the_identity() -> None:
    before = keys_of(control(dom_id="email", label="Email"))
    after = keys_of(control(dom_id="email", label="Email address"))
    assert before == after


def test_identity_does_not_depend_on_position() -> None:
    first = keys_of(control(dom_id="a"), control(dom_id="b"))
    second = keys_of(control(dom_id="new"), control(dom_id="a"), control(dom_id="b"))
    assert set(first) <= set(second)


def test_identical_controls_get_distinct_keys() -> None:
    keys = keys_of(control(name="x"), control(name="x"), control(name="x"))
    assert keys == ["field:name:x", "field:name:x#2", "field:name:x#3"]
    assert len(set(keys_of(*[control(label="Same") for _ in range(4)]))) == 4


def test_groups_are_identified_by_their_name() -> None:
    keys = keys_of(
        control("radio", "Q", name="auth", options=[RawOption("y", "Yes"), RawOption("n", "No")]),
        control("checkbox_group", "Tech", name="tech", options=[RawOption("a", "A")]),
    )
    assert keys == ["group:name:auth", "group:name:tech"]


def test_very_long_identities_are_shortened_deterministically() -> None:
    key = keys_of(control(name="n" * 400))[0]
    assert len(key) <= 255 and key == keys_of(control(name="n" * 400))[0]


def test_a_control_without_any_label_falls_back_to_what_the_page_calls_it() -> None:
    field = discover_fields(snapshot_of(control(label="", name="expected_salary")))[0]
    assert field.label == "expected salary"
    empty = discover_fields(snapshot_of(control(label="")))[0]
    assert empty.label == ""


def test_what_the_assistant_must_leave_to_the_user_is_marked() -> None:
    fields = {
        f.label: f
        for f in discover_fields(
            snapshot_of(
                control(
                    "checkbox", "I agree to the privacy policy", options=[RawOption("on", "x")]
                ),
                control("custom", "Pick a country", meta={"role": "combobox"}),
                control("month", "Graduation month"),
                control("text", "Locked", readonly=True),
                control("text", "Sign here (signature)"),
                control("file", "Resume", meta={"accept": ".pdf"}),
                control("text", "Phone number"),
            )
        )
    }
    assert fields["I agree to the privacy policy"].user_action_reason == "LEGAL_CONSENT"
    assert fields["Pick a country"].user_action_reason == "CUSTOM_CONTROL"
    assert fields["Graduation month"].user_action_reason == "UNSUPPORTED_TYPE"
    assert fields["Locked"].user_action_reason == "READ_ONLY"
    assert fields["Sign here (signature)"].user_action_reason == "LEGAL_CONSENT"
    assert (
        fields["Resume"].user_action_reason is None
        and fields["Resume"].detected_document_type == "tailored_cv"
    )
    assert fields["Phone number"].has_question and not fields["Resume"].has_question


def test_question_types_follow_the_control() -> None:
    def kind(raw: RawControl, **kw: Any) -> QuestionType:
        return question_type_of(discover_fields(snapshot_of(raw))[0], **kw)

    yes_no = [RawOption("y", "Yes"), RawOption("n", "No")]
    assert kind(control("text")) is QuestionType.FREE_TEXT
    assert kind(control("textarea")) is QuestionType.FREE_TEXT
    assert kind(control("number")) is QuestionType.NUMERIC
    assert kind(control("number"), topic_is_salary=True) is QuestionType.SALARY
    assert kind(control("date")) is QuestionType.DATE
    assert kind(control("radio", name="r", options=yes_no)) is QuestionType.YES_NO
    assert (
        kind(control("radio", name="r", options=list(reversed(yes_no))))
        is QuestionType.SINGLE_CHOICE
    )
    assert (
        kind(control("radio", name="r", options=[RawOption("ja", "Ja"), RawOption("nein", "Nein")]))
        is QuestionType.YES_NO
    )
    assert (
        kind(control("select", options=[RawOption("a", "A"), RawOption("b", "B")]))
        is QuestionType.SINGLE_CHOICE
    )
    assert kind(control("select")) is QuestionType.UNKNOWN
    assert (
        kind(control("checkbox_group", name="c", options=[RawOption("a", "A")]))
        is QuestionType.MULTI_CHOICE
    )
    assert kind(control("checkbox", options=[RawOption("on", "Yes")])) is QuestionType.YES_NO
    field = discover_fields(snapshot_of(control("radio", name="r", options=yes_no)))[0]
    assert question_options_of(field, QuestionType.YES_NO) == ["Yes", "No"]


# --- Mapping -------------------------------------------------------------------------------------


def question(
    text: str,
    *,
    qtype: QuestionType = QuestionType.FREE_TEXT,
    external_key: str | None = None,
    source: str = "manual",
    answer: str | None = None,
    max_length: int | None = None,
    options: list[str] | None = None,
) -> ApplicationQuestion:
    q = ApplicationQuestion(
        id=uuid.uuid4(),
        application_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        question_text=text,
        normalized_question_text=normalize_question(text),
        question_hash=question_hash(text),
        question_type=qtype,
        required=False,
        max_length=max_length,
        options=options,
        external_key=external_key,
        source=source,
        sort_order=0,
    )
    q.answer = ApplicationQuestionAnswer(
        id=uuid.uuid4(),
        user_id=q.user_id,
        status=AnswerStatus.USER_EDITED if answer else AnswerStatus.NOT_STARTED,
        answer_source=AnswerSource.USER if answer else None,
        final_answer=answer,
        ai_source_fact_ids=[],
        suggestion_dismissed=False,
    )
    return q


def decide(label: str, questions: list[ApplicationQuestion], kind: str = "text", **kw: Any) -> Any:
    item = discover_fields(snapshot_of(control(kind, label, dom_id="f1", **kw)))[0]
    return mapping.FieldMapper(questions, None, claimed=set()).decide(item)


def test_a_question_made_for_the_same_field_wins() -> None:
    made = question("Old label", external_key="browser:field:id:f1", source="browser")
    decision = decide("New label", [made, question("New label")])
    assert decision.question is made and decision.method == "IDENTITY"
    assert decision.status is MappingStatus.AUTO_MAPPED and decision.confidence == "HIGH"


def test_identical_words_map_and_different_words_do_not() -> None:
    mine = question("Phone number")
    assert decide("Phone number?", [mine]).question is mine
    other = decide("Reference code", [mine])
    assert other.question is None and other.create is True and other.method == "CREATED"


def test_the_same_topic_maps_across_languages() -> None:
    mine = question("Gehaltsvorstellung", qtype=QuestionType.SALARY)
    decision = decide("Expected annual salary", [mine], kind="number")
    assert decision.question is mine and decision.method == "TOPIC"


def test_two_equally_good_matches_wait_for_the_user() -> None:
    decision = decide("Phone number", [question("Phone number"), question("phone number")])
    assert decision.status is MappingStatus.NEEDS_REVIEW and decision.reason == "AMBIGUOUS_MATCH"
    assert decision.question is None and decision.create is False


def test_a_question_answers_one_field() -> None:
    only = question("Phone number")
    mapper = mapping.FieldMapper([only], None, claimed=set())
    first = discover_fields(snapshot_of(control("text", "Phone number", dom_id="a")))[0]
    second = discover_fields(snapshot_of(control("text", "Phone number", dom_id="b")))[0]
    assert mapper.decide(first).question is only
    assert mapper.decide(second).create is True  # already claimed


def test_an_incompatible_question_is_never_a_candidate() -> None:
    choice = question("Work model", qtype=QuestionType.SINGLE_CHOICE, options=["Remote", "Onsite"])
    assert decide("Work model", [choice], kind="text").create is True
    textual = question("Work model")
    decision = decide(
        "Work model",
        [textual],
        kind="select",
        options=[RawOption("r", "Remote"), RawOption("o", "Onsite")],
    )
    assert (
        decision.question is textual
    )  # free text can be answered into a select (checked at fill time)


def test_similar_but_not_equal_questions_are_proposed_not_applied() -> None:
    mine = question("Describe your motivation for joining our company")
    decision = decide("What is your motivation for joining us", [mine])
    assert decision.status is MappingStatus.NEEDS_REVIEW and decision.method == "SIMILAR"
    assert decision.proposed is mine and decision.question is None


def test_similarity_ignores_filler_words() -> None:
    assert (
        mapping.similarity("Why do you want to work for us?", "Why do you want to work for us")
        == 1.0
    )
    assert mapping.similarity("Phone number", "Years of experience") == 0.0
    assert mapping.similarity("", "x") == 0.0


def test_a_browser_question_follows_its_field_but_never_loses_the_answer() -> None:
    q = question("Email", external_key="browser:field:id:f1", source="browser", answer="a@b.c")
    field = discover_fields(snapshot_of(control("email", "Email address", dom_id="f1")))[0]
    outcome = mapping.refresh_browser_question(q, field)
    assert outcome.updated and q.question_text == "Email address"
    assert q.question_hash == question_hash("Email address")
    assert q.answer is not None and q.answer.final_answer == "a@b.c"

    short = discover_fields(
        snapshot_of(control("textarea", "Email address", dom_id="f1", max_length=2))
    )[0]
    refused = mapping.refresh_browser_question(q, short)
    assert refused.conflict and q.max_length is None  # the answer would no longer fit: unchanged


def test_a_users_own_question_is_never_rewritten() -> None:
    q = question("My wording", source="manual")
    field = discover_fields(snapshot_of(control("text", "Their wording", dom_id="f1")))[0]
    assert mapping.refresh_browser_question(q, field).updated is False
    assert q.question_text == "My wording"


def test_the_mapping_prompt_is_pinned() -> None:
    path = Path(__file__).resolve().parents[1] / "app" / "ai" / "prompts" / "field_mapping_v1.txt"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == prompts.PROMPT_FINGERPRINTS[prompts.FIELD_MAPPING_PROMPT_VERSION]
    )
    assert (
        prompts.current_prompt_hash()
        == prompts.PROMPT_FINGERPRINTS[prompts.FIELD_MAPPING_PROMPT_VERSION]
    )
    text = prompts.system_prompt()
    assert "never invent" in text.lower() and "null" in text


# --- Filling: what may go into a control ---------------------------------------------------------


def fld(kind: str, options: list[tuple[str, str]] | None = None, **kw: Any) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        input_type=kind,
        options=[{"value": v, "label": label} for v, label in options] if options else None,
        max_length=kw.get("max_length"),
        last_agent_value_hash=kw.get("last"),
    )


def approved(text: str | None = None, options: list[str] | None = None) -> ResolvedAnswer:
    from app.services.automation.resolver import AnswerState

    return ResolvedAnswer(AnswerState.APPROVED, "USER", text=text, options=options)


def test_text_goes_in_as_written_within_the_forms_limit() -> None:
    plan = filler.plan_value(fld("text", max_length=10), approved("  hello  "))
    assert isinstance(plan, filler.Intended) and plan.text == "hello"
    assert filler.plan_value(fld("text", max_length=3), approved("hello")) == filler.Unfillable(
        "ANSWER_TOO_LONG"
    )
    assert isinstance(filler.plan_value(fld("textarea"), approved("a\nb")), filler.Intended)
    assert filler.plan_value(fld("text"), approved("a\nb")) == filler.Unfillable(
        "UNSUPPORTED_VALUE"
    )


def test_numbers_and_dates_must_fit_their_controls() -> None:
    assert isinstance(filler.plan_value(fld("number"), approved("7.5")), filler.Intended)
    assert filler.plan_value(fld("number"), approved("seven")) == filler.Unfillable(
        "UNSUPPORTED_VALUE"
    )
    iso = filler.plan_value(fld("date"), approved("2027-01-01"))
    german = filler.plan_value(fld("date"), approved("01.02.2027"))
    assert isinstance(iso, filler.Intended) and iso.text == "2027-01-01"
    assert isinstance(german, filler.Intended) and german.text == "2027-02-01"
    assert filler.plan_value(fld("date"), approved("next spring")) == filler.Unfillable(
        "UNSUPPORTED_VALUE"
    )


def test_choices_are_matched_exactly_by_value_or_label() -> None:
    options = [("de", "Germany"), ("at", "Austria")]
    assert (
        filler.match_option([{"value": v, "label": label} for v, label in options], "austria")
        == "at"
    )
    assert filler.match_option([{"value": v, "label": label} for v, label in options], "DE") == "de"
    assert (
        filler.match_option([{"value": v, "label": label} for v, label in options], "Austr") is None
    )
    select = filler.plan_value(fld("select", options), approved(options=["Austria"]))
    assert isinstance(select, filler.Intended) and select.option_value == "at"
    assert filler.plan_value(fld("select", options), approved("Narnia")) == filler.Unfillable(
        "OPTION_NOT_FOUND"
    )
    assert filler.plan_value(
        fld("radio", options), approved(options=["Germany", "Austria"])
    ) == filler.Unfillable("UNSUPPORTED_VALUE")


def test_a_checkbox_takes_only_yes_or_no_and_a_group_every_listed_option() -> None:
    yes = filler.plan_value(fld("checkbox"), approved("Ja"))
    no = filler.plan_value(fld("checkbox"), approved("No"))
    assert isinstance(yes, filler.Intended) and yes.checked is True
    assert isinstance(no, filler.Intended) and no.checked is False
    assert filler.plan_value(fld("checkbox"), approved("maybe")) == filler.Unfillable(
        "UNSUPPORTED_VALUE"
    )
    group = filler.plan_value(
        fld("checkbox_group", [("p", "Python"), ("g", "Go")]), approved(options=["Go", "Python"])
    )
    assert isinstance(group, filler.Intended) and group.option_values == ("g", "p")
    assert filler.plan_value(
        fld("checkbox_group", [("p", "Python")]), approved(options=["Python", "Rust"])
    ) == filler.Unfillable("OPTION_NOT_FOUND")


@pytest.mark.parametrize(
    ("kind", "current", "intended", "last", "overwrite", "expected"),
    [
        # empty, never written: fill
        ("text", "", "x", None, False, "fill"),
        # already right: nothing to do
        ("text", "x", "x", None, False, "already_correct"),
        ("text", "x", "x", "anything", False, "already_correct"),
        # something there the assistant did not write: the user's
        ("text", "mine", "x", None, False, "user_modified"),
        # the assistant wrote it and it is unchanged: the answer changed, so replace
        ("text", "old", "new", "HASH_OF_old", False, "fill"),
        # the assistant wrote it and the user changed it
        ("text", "edited", "new", "HASH_OF_old", False, "user_modified"),
        # the assistant wrote it and the user cleared it
        ("text", "", "new", "HASH_OF_old", False, "user_modified"),
        # an explicit request
        ("text", "mine", "x", None, True, "fill"),
        ("text", "edited", "new", "HASH_OF_old", True, "fill"),
        # checkboxes: unchecked is empty
        ("checkbox", "0", "1", None, False, "fill"),
        ("checkbox", "1", "0", None, False, "user_modified"),
        ("checkbox", "0", "0", None, False, "already_correct"),
    ],
)
def test_the_decision_never_overwrites_what_the_user_changed(
    kind: str, current: str, intended: str, last: str | None, overwrite: bool, expected: str
) -> None:
    field = fld(kind)
    if last is not None:
        field.last_agent_value_hash = (
            filler.hash_value(field.id, last.removeprefix("HASH_OF_"))
            if last.startswith("HASH_OF_")
            else "x"
        )
    assert filler.decide(field, intended, current, overwrite=overwrite).action == expected


def test_stored_hashes_are_salted_per_field_and_never_the_value() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    assert filler.hash_value(a, "same") != filler.hash_value(b, "same")
    assert filler.hash_value(a, "same") == filler.hash_value(a, "same")
    assert "same" not in filler.hash_value(a, "same") and len(filler.hash_value(a, "x")) == 64


def test_canonical_forms_of_what_a_control_holds() -> None:
    assert filler.canon_of_value("text", FieldValue(True, text="a\r\nb")) == "a\nb"
    assert filler.canon_of_value("select", FieldValue(True, text="de", selected=["de"])) == "de"
    assert filler.canon_of_value("checkbox", FieldValue(True, checked=True)) == "1"
    assert filler.canon_of_value("checkbox_group", FieldValue(True, selected=["b", "a"])) == "a,b"
    assert filler.canon_of_value("file", FieldValue(True, files=[("cv.docx", 10)])) == "cv.docx:10"
    assert filler.canon_of_value("text", FieldValue(False)) == ""
    assert filler.is_empty_canon("checkbox", "0") and not filler.is_empty_canon("text", "0")
