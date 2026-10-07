"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { FieldRow } from "@/components/applications/assistant/FieldRow";
import { FinalReview } from "@/components/applications/assistant/FinalReview";
import { LiveView } from "@/components/applications/assistant/LiveView";
import { SubmissionResult } from "@/components/applications/assistant/SubmissionResult";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  clearDocument,
  confirmDomain,
  discoverFields,
  fillFields,
  getSession,
  listEvents,
  listFields,
  listSessions,
  pauseSession,
  resumeSession,
  selectDocument,
  setMapping,
  startSession,
  stopSession,
  suggestAnswers,
  takeControl,
} from "@/lib/api/automation";
import { listSubmissions } from "@/lib/api/submission";
import { STATUS_DOT, apiMessage, isActive, summaryLines } from "@/lib/automation";
import { safeExternalUrl } from "@/lib/jobs";
import type {
  AutomationEvent,
  AutomationSession,
  DocumentChoice,
  FieldList,
  FormField,
  MappingAction,
  SessionList,
} from "@/types/automation";
import type { SubmissionAttempt, SubmissionList } from "@/types/submission";

interface Props {
  applicationId: string;
}

const POLL_MS = 3000;
const ALERT_ATTENTION = ["SECURITY_CHALLENGE", "LOGIN_REQUIRED", "ACCESS_RESTRICTED"];

/**
 * The browser assistant of an application (V0.10).
 *
 *     JobPilot fills forms; you remain in control.
 *
 * It opens the application's own page in a controlled browser, reads the form, maps each
 * field to a question, and fills only the answers you approved -- each one checked after
 * it is written. You can pause it, take over, edit anything and hand the browser back; it
 * never overwrites what you changed and never ticks a consent on its own.
 *
 * V0.11: it never submits on its own either. The final review shows exactly what would be
 * sent; only your explicit, per-consent and final authorization lets JobPilot press the
 * employer's final button -- once.
 */
export function BrowserAssistant({ applicationId }: Props) {
  const [list, setList] = useState<SessionList | null>(null);
  const [session, setSession] = useState<AutomationSession | null>(null);
  const [fields, setFields] = useState<FieldList | null>(null);
  const [events, setEvents] = useState<AutomationEvent[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [submissions, setSubmissions] = useState<SubmissionList | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const loadFields = useCallback(
    async (sessionId: string) => {
      try {
        const loaded = await listFields(applicationId, sessionId);
        if (mounted.current) setFields(loaded);
      } catch {
        /* the next poll tries again */
      }
    },
    [applicationId]
  );

  const adopt = useCallback(
    (next: AutomationSession) => {
      setSession(next);
      void loadFields(next.id);
    },
    [loadFields]
  );

  const loadSubmissions = useCallback(async () => {
    try {
      const loaded = await listSubmissions(applicationId);
      if (mounted.current) setSubmissions(loaded);
    } catch {
      /* shown again on the next load */
    }
  }, [applicationId]);

  const load = useCallback(async () => {
    void loadSubmissions();
    try {
      const loaded = await listSessions(applicationId);
      if (!mounted.current) return;
      setList(loaded);
      setLoadError(null);
      const current = loaded.items.find((s) => isActive(s)) ?? loaded.items[0] ?? null;
      setSession(current);
      if (current) void loadFields(current.id);
    } catch {
      if (mounted.current) setLoadError("Unable to load the browser assistant. Please try again.");
    }
  }, [applicationId, loadFields, loadSubmissions]);

  useEffect(() => {
    void load();
  }, [load]);

  function onAttempt(attempt: SubmissionAttempt) {
    setSubmissions((current) => ({
      items: [attempt, ...(current?.items.filter((a) => a.id !== attempt.id) ?? [])],
      submitted: (current?.submitted ?? false) || attempt.result === "SUCCESS",
      needs_verification: attempt.result === "SUBMISSION_UNKNOWN",
    }));
    void load();
  }

  // While the assistant is live, keep the view current (the page may move, the user may
  // edit fields, the browser may be closed).
  const active = isActive(session);
  const sessionId = session?.id;
  useEffect(() => {
    if (!active || !sessionId) return;
    const timer = setInterval(async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        const fresh = await getSession(applicationId, sessionId);
        if (!mounted.current) return;
        setSession(fresh);
        // The session ended (a submission ends it too): show what came of it.
        if (!isActive(fresh)) void loadSubmissions();
        await loadFields(sessionId);
      } catch {
        /* a failed poll changes nothing */
      } finally {
        inFlight.current = false;
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [active, sessionId, applicationId, loadFields, loadSubmissions]);

  async function run(
    name: string,
    action: () => Promise<AutomationSession>,
    success?: (next: AutomationSession) => string | null
  ) {
    setBusy(name);
    setError(null);
    setNotice(null);
    try {
      const next = await action();
      if (!mounted.current) return;
      adopt(next);
      setNotice(success ? success(next) : null);
      if (!isActive(next)) void load();
    } catch (e) {
      if (mounted.current) setError(apiMessage(e, "That did not work. Please try again."));
      void load();
    } finally {
      if (mounted.current) setBusy(null);
    }
  }

  async function suggest() {
    if (!session) return;
    setBusy("suggest");
    setError(null);
    setNotice(null);
    try {
      const result = await suggestAnswers(applicationId, session.id);
      if (!mounted.current) return;
      adopt(result.session);
      setNotice(
        result.requested === 0
          ? "There is nothing to suggest an answer for."
          : `Suggestions are ready for ${result.suggested} of ${result.requested} questions. Review them in Application questions: nothing is filled until you accept it.`
      );
    } catch (e) {
      if (mounted.current) setError(apiMessage(e, "Unable to suggest answers."));
    } finally {
      if (mounted.current) setBusy(null);
    }
  }

  async function onMapping(field: FormField, decision: MappingAction) {
    if (!session) return;
    try {
      await setMapping(applicationId, session.id, field.id, decision);
      await loadFields(session.id);
      const fresh = await getSession(applicationId, session.id);
      if (mounted.current) setSession(fresh);
    } catch (e) {
      if (mounted.current) setError(apiMessage(e, "Unable to change the mapping."));
    }
  }

  async function onRefill(field: FormField) {
    if (!session) return;
    await run("fill", () =>
      fillFields(applicationId, session.id, {
        field_ids: [field.id],
        overwrite_field_ids: [field.id],
      })
    );
  }

  async function onUseDocument(field: FormField, choice: DocumentChoice) {
    if (!session) return;
    await selectDocument(applicationId, session.id, field.id, choice.type, choice.id);
    await loadFields(session.id);
  }

  async function onClearDocument(field: FormField) {
    if (!session) return;
    try {
      await clearDocument(applicationId, session.id, field.id);
      await loadFields(session.id);
    } catch (e) {
      setError(apiMessage(e, "Unable to remove the file choice."));
    }
  }

  async function showActivity() {
    if (!session) return;
    try {
      const loaded = await listEvents(applicationId, session.id);
      if (mounted.current) setEvents(loaded.items);
    } catch {
      if (mounted.current) setEvents([]);
    }
  }

  const applicationPage = safeExternalUrl(session?.target_url);
  const live = isActive(session);
  const controls = session?.controls;
  const locked = !controls?.can_fill;

  return (
    <Card aria-labelledby="browser-assistant-heading" id="browser-assistant">
      <h2 id="browser-assistant-heading" className="text-lg font-semibold text-slate-900">
        Browser assistant
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        JobPilot fills forms; you remain in control. It opens this application&apos;s own page in
        a browser, reads the form and fills only the answers you approved. You can pause it, take
        over and change anything at any time. It never submits your application on its own:
        only after your final review and your explicit authorization — and then exactly once.
      </p>

      {loadError && (
        <div className="mt-4">
          <Alert variant="error">{loadError}</Alert>
          <Button className="mt-2" variant="secondary" onClick={() => void load()}>
            Try again
          </Button>
        </div>
      )}
      {!list && !loadError && <p className="mt-4 text-sm text-slate-500">Loading browser assistant...</p>}

      {list && (
        <div className="mt-4 flex flex-col gap-4">
          {error && <Alert variant="error">{error}</Alert>}
          {notice && <Alert variant="info">{notice}</Alert>}

          {submitting && (
            <p role="status" className="text-sm font-medium text-slate-900">
              Submitting... JobPilot presses the final button once and waits for the
              employer&apos;s confirmation.
            </p>
          )}
          {!submitting && submissions?.items[0] && (
            <SubmissionResult
              applicationId={applicationId}
              attempt={submissions.items[0]}
              onChanged={() => void loadSubmissions()}
            />
          )}

          {session && (
            <section aria-label="Browser assistant status" className="flex flex-col gap-3">
              <p
                role="status"
                data-testid="assistant-status"
                className="flex items-center gap-2 text-sm text-slate-900"
              >
                <span
                  aria-hidden="true"
                  className={`inline-block h-2.5 w-2.5 rounded-full ${STATUS_DOT[session.status]}`}
                />
                <strong>{session.status_label}</strong>
              </p>

              {live && (
                <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
                  <div className="col-span-2 sm:col-span-3">
                    <dt className="inline text-slate-500">Current page: </dt>
                    <dd className="inline text-slate-900">
                      {session.page_title || session.current_url || "Starting..."}
                    </dd>
                  </div>
                  {summaryLines(session).map(([label, value]) => (
                    <div key={label}>
                      <dt className="inline text-slate-500">{label}: </dt>
                      <dd className="inline font-medium text-slate-900">{value}</dd>
                    </div>
                  ))}
                </dl>
              )}

              {session.attention && (
                <Alert variant={ALERT_ATTENTION.includes(session.attention.code) ? "error" : "info"}>
                  {session.attention.message}
                </Alert>
              )}
              {session.submit_notice && <Alert variant="info">{session.submit_notice}</Alert>}
              {session.status === "FAILED" && (
                <Alert variant="error">
                  {session.last_error?.message ?? "The browser session failed."}
                </Alert>
              )}
              {session.status === "STOPPED" && (
                <Alert variant="info">
                  The assistant has stopped and no longer touches the browser.
                  {session.last_error ? ` ${session.last_error.message}` : ""} You can continue by
                  hand{applicationPage ? " on the application website" : ""} or start the assistant
                  again.
                </Alert>
              )}

              {live && controls && (
                <div className="flex flex-wrap gap-2" role="group" aria-label="Assistant controls">
                  {controls.can_pause && (
                    <Button
                      variant="secondary"
                      isLoading={busy === "pause"}
                      onClick={() => void run("pause", () => pauseSession(applicationId, session.id))}
                    >
                      Pause
                    </Button>
                  )}
                  {controls.can_take_control && (
                    <Button
                      variant="secondary"
                      isLoading={busy === "control"}
                      onClick={() => void run("control", () => takeControl(applicationId, session.id))}
                    >
                      Take control
                    </Button>
                  )}
                  {controls.can_resume && (
                    <Button
                      isLoading={busy === "resume"}
                      onClick={() =>
                        void run("resume", () => resumeSession(applicationId, session.id, true))
                      }
                    >
                      Resume assistant
                    </Button>
                  )}
                  {controls.can_confirm_domain && session.current_url && (
                    <Button
                      isLoading={busy === "domain"}
                      onClick={() =>
                        void run("domain", () =>
                          confirmDomain(applicationId, session.id, new URL(session.current_url!).hostname)
                        )
                      }
                    >
                      This website is part of my application
                    </Button>
                  )}
                  {controls.can_stop && (
                    <Button
                      variant="secondary"
                      isLoading={busy === "stop"}
                      onClick={() => void run("stop", () => stopSession(applicationId, session.id, false))}
                    >
                      Stop assistant
                    </Button>
                  )}
                  {controls.can_stop && (
                    <Button
                      variant="secondary"
                      isLoading={busy === "stop-close"}
                      onClick={() =>
                        void run("stop-close", () => stopSession(applicationId, session.id, true))
                      }
                    >
                      Stop and close the browser
                    </Button>
                  )}
                </div>
              )}

              {live && controls && (controls.can_fill || controls.can_discover) && (
                <div className="flex flex-wrap items-center gap-2">
                  {controls.can_fill && (
                    <Button
                      isLoading={busy === "fill"}
                      onClick={() => void run("fill", () => fillFields(applicationId, session.id))}
                    >
                      Fill approved answers
                    </Button>
                  )}
                  {controls.can_discover && (
                    <Button
                      variant="secondary"
                      isLoading={busy === "discover"}
                      onClick={() =>
                        void run("discover", () => discoverFields(applicationId, session.id))
                      }
                    >
                      Rediscover fields
                    </Button>
                  )}
                  {controls.can_fill && (
                    <Button
                      variant="secondary"
                      isLoading={busy === "suggest"}
                      onClick={() => void suggest()}
                    >
                      Suggest answers
                    </Button>
                  )}
                  <span className="text-xs text-slate-500">
                    Only approved answers are filled. Fields you changed are left alone.
                  </span>
                </div>
              )}

              {live && controls && !controls.can_fill && session.status === "USER_CONTROL" && (
                <p className="text-sm text-slate-600">
                  You are in control of the browser. Nothing will change until you press{" "}
                  <strong>Resume assistant</strong>.
                </p>
              )}

              {live && <LiveView applicationId={applicationId} session={session} />}

              {live &&
                controls &&
                (session.status === "RUNNING" || session.status === "COMPLETED") &&
                !submissions?.submitted && (
                  <FinalReview
                    applicationId={applicationId}
                    session={session}
                    onTakeControl={() =>
                      void run("control", () => takeControl(applicationId, session.id))
                    }
                    onAttempt={onAttempt}
                    onSubmitting={setSubmitting}
                  />
                )}
            </section>
          )}

          {(!session || !live) && (
            <div className="flex flex-col gap-2">
              <div>
                <Button
                  isLoading={busy === "start"}
                  disabled={!list.can_start}
                  onClick={() =>
                    void run("start", () => startSession(applicationId), (next) =>
                      next.status === "RUNNING"
                        ? `Found ${next.counts.found} fields. Review them below, then fill the approved answers.`
                        : null
                    )
                  }
                >
                  Start browser assistant
                </Button>
              </div>
              {!list.can_start && list.cannot_start_message && (
                <p className="text-sm text-slate-600">{list.cannot_start_message}</p>
              )}
              {list.can_start && (
                <p className="text-sm text-slate-500">
                  This opens {applicationPage ? "the application website" : "the application page"}{" "}
                  in a browser. Nothing is filled until you say so.
                </p>
              )}
            </div>
          )}

          {fields && live && fields.items.length > 0 && (
            <section aria-label="Discovered fields" className="flex flex-col gap-2">
              <h3 className="text-base font-semibold text-slate-900">Form fields</h3>
              <div className="overflow-x-auto">
                <table aria-label="Form fields" className="w-full min-w-[40rem] text-left">
                  <thead>
                    <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                      <th scope="col" className="py-2 pr-3 font-medium">
                        Field
                      </th>
                      <th scope="col" className="py-2 pr-3 font-medium">
                        Question
                      </th>
                      <th scope="col" className="py-2 font-medium">
                        Status
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {fields.items.map((field) => (
                      <FieldRow
                        key={field.id}
                        applicationId={applicationId}
                        sessionId={session!.id}
                        field={field}
                        questions={fields.questions}
                        locked={locked}
                        onMapping={onMapping}
                        onRefill={onRefill}
                        onUseDocument={onUseDocument}
                        onClearDocument={onClearDocument}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {session && (
            <details
              className="text-sm"
              onToggle={(e) => {
                if ((e.currentTarget as HTMLDetailsElement).open) void showActivity();
              }}
            >
              <summary className="cursor-pointer font-medium text-slate-700">Activity</summary>
              {events === null ? (
                <p className="mt-1 text-slate-500">Loading...</p>
              ) : events.length === 0 ? (
                <p className="mt-1 text-slate-500">Nothing yet.</p>
              ) : (
                <ul className="mt-1 list-disc pl-5 text-slate-600">
                  {events.map((event) => (
                    <li key={event.id}>{event.event_type.toLowerCase().replace(/_/g, " ")}</li>
                  ))}
                </ul>
              )}
            </details>
          )}
        </div>
      )}
    </Card>
  );
}
