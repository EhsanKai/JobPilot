import { getStoredToken } from "@/lib/auth";
import { ApiError, api } from "@/lib/http";
import type {
  AutomationEvent,
  AutomationSession,
  DocumentChoice,
  DocumentType,
  FieldList,
  FillOptions,
  FormField,
  MappingAction,
  SessionList,
  SuggestResult,
} from "@/types/automation";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

function base(applicationId: string): string {
  return `/applications/${encodeURIComponent(applicationId)}/automation`;
}

function one(applicationId: string, sessionId: string): string {
  return `${base(applicationId)}/${encodeURIComponent(sessionId)}`;
}

function field(applicationId: string, sessionId: string, fieldId: string): string {
  return `${one(applicationId, sessionId)}/fields/${encodeURIComponent(fieldId)}`;
}

export function listSessions(applicationId: string): Promise<SessionList> {
  return api.get<SessionList>(base(applicationId));
}

/** Open the application's page in a controlled browser. The page is the application's own URL. */
export function startSession(applicationId: string): Promise<AutomationSession> {
  return api.post<AutomationSession>(`${base(applicationId)}/start`);
}

export function getSession(applicationId: string, sessionId: string): Promise<AutomationSession> {
  return api.get<AutomationSession>(one(applicationId, sessionId));
}

export function listFields(applicationId: string, sessionId: string): Promise<FieldList> {
  return api.get<FieldList>(`${one(applicationId, sessionId)}/fields`);
}

export function listEvents(
  applicationId: string,
  sessionId: string
): Promise<{ items: AutomationEvent[] }> {
  return api.get<{ items: AutomationEvent[] }>(`${one(applicationId, sessionId)}/events?limit=30`);
}

/** Stop the assistant from changing anything. */
export function pauseSession(applicationId: string, sessionId: string): Promise<AutomationSession> {
  return api.post<AutomationSession>(`${one(applicationId, sessionId)}/pause`);
}

/** The browser is the user's: nothing is changed until they resume. */
export function takeControl(applicationId: string, sessionId: string): Promise<AutomationSession> {
  return api.post<AutomationSession>(`${one(applicationId, sessionId)}/take-control`);
}

/** Return control to the assistant. It looks first; with `fill` it then fills what is still empty. */
export function resumeSession(
  applicationId: string,
  sessionId: string,
  fill = false
): Promise<AutomationSession> {
  return api.post<AutomationSession>(`${one(applicationId, sessionId)}/resume`, { fill });
}

export function stopSession(
  applicationId: string,
  sessionId: string,
  closeBrowser = false
): Promise<AutomationSession> {
  return api.post<AutomationSession>(`${one(applicationId, sessionId)}/stop`, {
    close_browser: closeBrowser,
  });
}

export function confirmDomain(
  applicationId: string,
  sessionId: string,
  host: string
): Promise<AutomationSession> {
  return api.post<AutomationSession>(`${one(applicationId, sessionId)}/confirm-domain`, { host });
}

/** Read the page again. Reads only. */
export function discoverFields(
  applicationId: string,
  sessionId: string
): Promise<AutomationSession> {
  return api.post<AutomationSession>(`${one(applicationId, sessionId)}/discover`);
}

/** Fill the approved answers. Never overwrites what the user changed, never submits. */
export function fillFields(
  applicationId: string,
  sessionId: string,
  options: FillOptions = {}
): Promise<AutomationSession> {
  return api.post<AutomationSession>(`${one(applicationId, sessionId)}/fill`, options);
}

export function suggestAnswers(applicationId: string, sessionId: string): Promise<SuggestResult> {
  return api.post<SuggestResult>(`${one(applicationId, sessionId)}/suggest`);
}

export function setMapping(
  applicationId: string,
  sessionId: string,
  fieldId: string,
  decision: MappingAction
): Promise<FormField> {
  return api.post<FormField>(`${field(applicationId, sessionId, fieldId)}/mapping`, decision);
}

export function listDocumentChoices(
  applicationId: string,
  sessionId: string,
  fieldId: string
): Promise<{ items: DocumentChoice[] }> {
  return api.get<{ items: DocumentChoice[] }>(
    `${field(applicationId, sessionId, fieldId)}/documents`
  );
}

/** "Use this file": pin one document of this application to a file field. */
export function selectDocument(
  applicationId: string,
  sessionId: string,
  fieldId: string,
  documentType: DocumentType,
  documentId: string
): Promise<FormField> {
  return api.put<FormField>(`${field(applicationId, sessionId, fieldId)}/document`, {
    document_type: documentType,
    document_id: documentId,
  });
}

export function clearDocument(
  applicationId: string,
  sessionId: string,
  fieldId: string
): Promise<FormField> {
  return api.delete<FormField>(`${field(applicationId, sessionId, fieldId)}/document`);
}

/**
 * A PNG of what the browser shows now, as an object URL for an <img>. Fetched with the
 * user's token (an <img src> cannot send it), never cached by the server.
 */
export async function fetchScreenshot(applicationId: string, sessionId: string): Promise<string> {
  const token = getStoredToken();
  const response = await fetch(`${API_BASE_URL}${one(applicationId, sessionId)}/screenshot`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: "no-store",
  });
  if (!response.ok) {
    throw new ApiError(response.status, "SCREENSHOT_UNAVAILABLE", "No live view available.", {});
  }
  return URL.createObjectURL(await response.blob());
}
