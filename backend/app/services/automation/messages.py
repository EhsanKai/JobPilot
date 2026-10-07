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
    "The application appears ready for submission. Without your explicit authorization, "
    "JobPilot does not submit applications. Please submit manually, or open Final review "
    "to check everything and authorize JobPilot to submit it."
)

#: What came of a submission attempt, in the user's words (V0.11).
SUBMISSION_RESULT_MESSAGES = {
    "SUCCESS": "Application submitted successfully.",
    "FAILED_BEFORE_SUBMIT": (
        "The application could not be submitted. No submission was confirmed -- the final "
        "button was not pressed."
    ),
    "SUBMISSION_UNKNOWN": (
        "We could not safely determine whether the application was submitted. For your "
        "safety, JobPilot will NOT submit again automatically. Please verify the application "
        "manually."
    ),
    "IN_PROGRESS": "Submitting...",
}

#: Why an attempt ended as it did (`SubmissionAttempt.failure_code`).
SUBMISSION_FAILURES = {
    "STATE_CHANGED": "The application changed after your review. Review it again.",
    "PAGE_RELOADED": "The page was reloaded or changed after your review. Review it again.",
    "WRONG_DESTINATION": "The browser is no longer on the reviewed website.",
    "SUBMISSION_BLOCKED": "Something on the page needs you first (see the final review).",
    "USER_TOOK_CONTROL": "You took control of the browser, so JobPilot did not submit.",
    "SESSION_NOT_READY": "The browser assistant was not running.",
    "BROWSER_GONE": "The browser was closed before anything was submitted.",
    "SUBMIT_CONTROL_NOT_FOUND": "The final button was no longer on the page.",
    "SUBMIT_CONTROL_DISABLED": "The final button was disabled.",
    "SUBMIT_CONTROL_NOT_READY": "The final button could not be pressed (hidden or covered).",
    "NOT_A_SUBMIT_CONTROL": "The final button was not a form button.",
    "INTERRUPTED": "The server stopped before the final button was pressed.",
    "NO_CONFIRMATION": "The page did not show a confirmation after the final button was pressed.",
    "BROWSER_DISCONNECTED": "The browser closed or crashed after the final button was pressed.",
    "SUBMIT_ERROR": "The browser reported an error while the final button was pressed.",
    "INTERRUPTED_AFTER_PRESS": "The server stopped after the final button was pressed.",
    "SUBMIT_ERROR_BEFORE_PRESS": "An error occurred before the final button was pressed.",
    "PERMIT_REFUSED": "The submission was refused before the final button was pressed.",
}

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
