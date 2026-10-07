import type {
  AutomationSession,
  DocumentChoice,
  FieldDocument,
  FieldList,
  FormField,
  QuestionBrief,
  SessionCounts,
  SessionList,
} from "@/types/automation";

let counter = 0;

export function makeCounts(overrides: Partial<SessionCounts> = {}): SessionCounts {
  return {
    found: 0,
    ready: 0,
    filled: 0,
    needs_review: 0,
    needs_input: 0,
    user_modified: 0,
    action_required: 0,
    failed: 0,
    unmapped: 0,
    ...overrides,
  };
}

const RUNNING_CONTROLS = {
  can_pause: true,
  can_resume: false,
  can_take_control: true,
  can_stop: true,
  can_discover: true,
  can_fill: true,
  can_confirm_domain: false,
};

const NO_CONTROLS = {
  can_pause: false,
  can_resume: false,
  can_take_control: false,
  can_stop: false,
  can_discover: false,
  can_fill: false,
  can_confirm_domain: false,
};

export function makeSession(overrides: Partial<AutomationSession> = {}): AutomationSession {
  counter += 1;
  return {
    id: `session-${counter}`,
    application_id: "app-1",
    status: "RUNNING",
    status_label: "Running",
    target_url: "https://jobs.example.com/apply",
    current_url: "https://jobs.example.com/apply",
    page_title: "Application form",
    browser_mode: "visible",
    browser_alive: true,
    attention: null,
    submit_detected: false,
    submit_notice: null,
    last_error: null,
    counts: makeCounts({ found: 14, filled: 9, needs_review: 3, needs_input: 2 }),
    controls: RUNNING_CONTROLS,
    started_at: new Date().toISOString(),
    ended_at: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

export function pausedSession(overrides: Partial<AutomationSession> = {}) {
  return makeSession({
    status: "PAUSED",
    status_label: "Paused",
    controls: { ...RUNNING_CONTROLS, can_pause: false, can_resume: true, can_fill: false, can_discover: false },
    ...overrides,
  });
}

export function userControlSession(overrides: Partial<AutomationSession> = {}) {
  return makeSession({
    status: "USER_CONTROL",
    status_label: "User control",
    controls: {
      ...RUNNING_CONTROLS,
      can_pause: false,
      can_take_control: false,
      can_resume: true,
      can_fill: false,
      can_discover: false,
    },
    ...overrides,
  });
}

export function blockedSession(overrides: Partial<AutomationSession> = {}) {
  return makeSession({
    status: "BLOCKED",
    status_label: "Blocked",
    attention: {
      code: "SECURITY_CHALLENGE",
      message:
        "The website is showing a security check (such as a CAPTCHA). JobPilot does not solve or work around these. Please take control of the browser and complete it yourself.",
    },
    counts: makeCounts(),
    controls: {
      ...NO_CONTROLS,
      can_take_control: true,
      can_stop: true,
    },
    ...overrides,
  });
}

export function failedSession(overrides: Partial<AutomationSession> = {}) {
  return makeSession({
    status: "FAILED",
    status_label: "Failed",
    last_error: { code: "NAVIGATION_FAILED", message: "The page could not be loaded." },
    counts: makeCounts(),
    controls: NO_CONTROLS,
    ended_at: new Date().toISOString(),
    ...overrides,
  });
}

export function stoppedSession(overrides: Partial<AutomationSession> = {}) {
  return makeSession({
    status: "STOPPED",
    status_label: "Stopped",
    controls: NO_CONTROLS,
    ended_at: new Date().toISOString(),
    ...overrides,
  });
}

export function makeSessionList(
  items: AutomationSession[] = [],
  overrides: Partial<SessionList> = {}
): SessionList {
  return {
    items,
    can_start: items.every((s) => !["RUNNING", "PAUSED", "USER_CONTROL", "BLOCKED", "COMPLETED", "STARTING"].includes(s.status)),
    cannot_start_reason: null,
    cannot_start_message: null,
    ...overrides,
  };
}

export function makeQuestion(overrides: Partial<QuestionBrief> = {}): QuestionBrief {
  counter += 1;
  return {
    id: `q-${counter}`,
    question_text: "Email address",
    question_type: "FREE_TEXT",
    required: false,
    source: "browser",
    answer_status: "NOT_STARTED",
    ...overrides,
  };
}

export function makeField(overrides: Partial<FormField> = {}): FormField {
  counter += 1;
  const label = overrides.label ?? "Email address";
  return {
    id: `field-${counter}`,
    field_key: `field:id:f${counter}`,
    position: counter,
    input_type: "email",
    label,
    name: null,
    dom_id: null,
    form_context: null,
    required: false,
    max_length: null,
    options: null,
    present: true,
    display_status: "ready",
    display_message: "Approved. It will be filled when you run the assistant.",
    required_missing: false,
    mapping: { status: "AUTO_MAPPED", method: "CREATED", confidence: "HIGH", reason: "NEW_QUESTION" },
    question: makeQuestion({ question_text: label }),
    proposed_question: null,
    answer: { state: "APPROVED", source: "DETERMINISTIC", text: "jane@example.com", options: null, reason: null, message: null },
    fill: { status: "NOT_FILLED", error_code: null, error_message: null, last_filled_at: null },
    user_action_reason: null,
    user_action_message: null,
    document: null,
    first_seen_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    ...overrides,
  };
}

export function makeFieldList(items: FormField[] = [], questions: QuestionBrief[] = []): FieldList {
  return { items, questions };
}

export function makeChoice(overrides: Partial<DocumentChoice> = {}): DocumentChoice {
  counter += 1;
  return {
    type: "tailored_cv",
    id: `doc-${counter}`,
    label: "Tailored CV v3",
    version_number: 3,
    origin: "ai",
    is_current: true,
    file_name: "Jane Doe - CV.docx",
    ...overrides,
  };
}

export function makeFileDocument(overrides: Partial<FieldDocument> = {}): FieldDocument {
  return {
    detected_type: "tailored_cv",
    proposed: makeChoice(),
    selected: null,
    confirmed: false,
    newer_available: false,
    format_accepted: true,
    ...overrides,
  };
}
