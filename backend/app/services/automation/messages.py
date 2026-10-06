"""What the assistant tells the user, in plain words (V0.10).

Codes are stable (the frontend and tests key on them); the texts are the user's.
None of them contains a form value, a page's text or a URL query.
"""

#: What needs the user's attention right now (`AutomationSession.attention_code`).
LOGIN_REQUIRED = "LOGIN_REQUIRED"
SECURITY_CHALLENGE = "SECURITY_CHALLENGE"
UNEXPECTED_DOMAIN = "UNEXPECTED_DOMAIN"
ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
NO_FORM_FOUND = "NO_FORM_FOUND"

ATTENTION_MESSAGES = {
    LOGIN_REQUIRED: "Login required. Please take control of the browser and sign in manually.",
    SECURITY_CHALLENGE: (
        "The website is showing a security check (such as a CAPTCHA). JobPilot does not solve "
        "or work around these. Please take control of the browser and complete it yourself."
    ),
    UNEXPECTED_DOMAIN: (
        "The browser moved to a different website than the one this application started on. "
        "Confirm that it is part of your application, or take control."
    ),
    ACCESS_RESTRICTED: (
        "The website did not allow access to this page. JobPilot does not try to get around "
        "that. Open the page yourself in the browser, or stop the assistant."
    ),
    NO_FORM_FOUND: (
        "No application form was found on this page. Open the form yourself in the browser "
        "(JobPilot never presses Apply for you), then choose Rediscover fields."
    ),
}

SUBMISSION_NOTICE = (
    "The application appears ready for submission. V0.10 does not submit applications. "
    "Please submit manually."
)

#: Why a field was not filled / failed (`AutomationField.fill_error_code`).
FILL_ERRORS = {
    "FIELD_NOT_FOUND": "The field is no longer on the page.",
    "NOT_EDITABLE": "The field cannot be edited.",
    "VALUE_MISMATCH": "The browser shows a different value than the approved answer.",
    "WRITE_FAILED": "The browser would not accept the value.",
    "OPTION_NOT_FOUND": "The approved answer is not one of the options the form offers.",
    "ANSWER_TOO_LONG": (
        "The approved answer is longer than the form allows. JobPilot never shortens it."
    ),
    "UNSUPPORTED_VALUE": "The approved answer cannot be entered in this kind of field.",
    "DOCUMENT_UNAVAILABLE": "The chosen document is no longer available.",
    "FILE_REJECTED": "The form did not accept the file.",
    "FILE_TYPE_NOT_ACCEPTED": (
        "The form does not accept this kind of file (JobPilot attaches DOCX)."
    ),
    "TIMEOUT": "The page did not respond in time.",
    "FILE_UNCLEAR": "It is not clear which document this field asks for. Choose one yourself.",
}

#: Why only the user can do a field (`AutomationField.user_action_reason`).
USER_ACTION_REASONS = {
    "LEGAL_CONSENT": (
        "A legal confirmation or consent. Only you can give it: JobPilot never ticks these."
    ),
    "CUSTOM_CONTROL": "A custom control JobPilot cannot fill safely. Please fill it yourself.",
    "READ_ONLY": "The field is read-only or disabled.",
    "UNSUPPORTED_TYPE": "A kind of field JobPilot does not fill. Please fill it yourself.",
    "FILE_NOT_CONFIRMED": "Choose which file to attach, then confirm it.",
    "FILE_UNCLEAR": "It is not clear which document this field asks for. Choose one yourself.",
}
