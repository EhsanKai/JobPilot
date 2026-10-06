"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { ApplicationDocuments } from "@/components/applications/ApplicationDocuments";
import { ApplicationQuestions } from "@/components/applications/ApplicationQuestions";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { getApplication, updateApplication } from "@/lib/api/applications";
import { listResumes } from "@/lib/api/resumes";
import {
  APPLICATION_LANGUAGE_OPTIONS,
  APPLICATION_STATUS_LABELS,
  FIELD_IDS,
  NOTICE_UNIT_OPTIONS,
  PREPARATION_LABELS,
  WORK_AUTHORIZATION_OPTIONS,
  buildPatch,
  fieldErrorsFromApi,
  firstInvalidField,
  formFromApplication,
  hasChanges,
  validateForm,
  type ApplicationForm,
  type FormErrors,
  type FormField,
} from "@/lib/applications";
import { ApiError } from "@/lib/http";
import {
  EMPLOYMENT_TYPE_LABELS,
  WORK_MODEL_LABELS,
  formatDate,
  formatSalary,
  safeExternalUrl,
  sourceLabel,
} from "@/lib/jobs";
import { RECOMMENDATION_STYLES, percent, staleText } from "@/lib/match";
import type { Application, PreparationProgress } from "@/types/application";
import type { MatchSummary } from "@/types/match";
import type { Resume } from "@/types/resume";

const SELECT_CLASS =
  "rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500";
const NOT_STATED = "Not stated";

/** Moves keyboard focus (and scroll) to a control, so a failed save leads to the problem. */
function focusField(field: FormField) {
  document.getElementById(FIELD_IDS[field])?.focus();
}

/** Accessibility attributes of a control that has an error (the text inputs get theirs from `Input`). */
function invalidProps(field: FormField, errors: FormErrors) {
  return errors[field]
    ? { "aria-invalid": true, "aria-describedby": `${FIELD_IDS[field]}-error` }
    : {};
}

function FieldError({ field, errors }: { field: FormField; errors: FormErrors }) {
  const message = errors[field];
  if (!message) return null;
  return (
    <p id={`${FIELD_IDS[field]}-error`} className="text-sm text-red-600">
      {message}
    </p>
  );
}

/**
 * The user's private workspace for one application. It prepares what later
 * steps need (salary expectation, availability, notice period, language,
 * resume, notes) and shows the job as it was when the application started.
 * Nothing here submits anything or contacts an employer.
 */
export function ApplicationWorkspace({ applicationId }: { applicationId: string }) {
  const [application, setApplication] = useState<Application | null>(null);
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [form, setForm] = useState<ApplicationForm | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FormErrors>({});
  const [isSaved, setIsSaved] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);
    setLoadError(null);
    Promise.all([
      getApplication(applicationId),
      // The resume list only feeds the "change resume" choice: not essential.
      listResumes().catch(() => [] as Resume[]),
    ])
      .then(([loaded, versions]) => {
        if (!isMounted) return;
        setApplication(loaded);
        setForm(formFromApplication(loaded));
        setResumes(versions);
      })
      .catch((err) => {
        if (!isMounted) return;
        setLoadError(
          err instanceof ApiError && err.status === 404
            ? "This application doesn't exist or isn't yours."
            : "Unable to load this application. Please try again."
        );
      })
      .finally(() => isMounted && setIsLoading(false));
    return () => {
      isMounted = false;
    };
  }, [applicationId]);

  function setField<K extends FormField>(field: K, value: ApplicationForm[K]) {
    setForm((current) => (current ? { ...current, [field]: value } : current));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
    setIsSaved(false);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!application || !form) return;
    setSaveError(null);
    setIsSaved(false);

    const errors = validateForm(form);
    setFieldErrors(errors);
    const invalid = firstInvalidField(errors);
    if (invalid) {
      setSaveError("Please fix the highlighted fields.");
      focusField(invalid);
      return;
    }
    const patch = buildPatch(application, form);
    if (!hasChanges(patch)) return;

    setIsSaving(true);
    try {
      const updated = await updateApplication(application.id, patch);
      setApplication(updated);
      setForm(formFromApplication(updated));
      setIsSaved(true);
    } catch (err) {
      if (err instanceof ApiError) {
        const { fields, general } = fieldErrorsFromApi(err);
        setFieldErrors(fields);
        const invalid = firstInvalidField(fields);
        setSaveError(invalid ? "Please fix the highlighted fields." : general);
        if (invalid) focusField(invalid);
      } else {
        setSaveError("Unable to save your changes. Please try again.");
      }
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading) return <p className="text-sm text-slate-500">Loading application...</p>;
  if (!application || !form) {
    return (
      <div className="flex flex-col gap-4">
        <Alert variant="error">{loadError ?? "Application not found."}</Alert>
        <Link href="/jobs" className="text-sm font-medium text-brand-700 hover:underline">
          ← Back to jobs
        </Link>
      </div>
    );
  }

  const { job } = application.context;
  const dirty = hasChanges(buildPatch(application, form));
  const canChooseResume = resumes.length > 1 || (resumes.length === 1 && !application.resume);

  return (
    <div className="flex flex-col gap-6">
      <Link
        href={`/jobs/${application.job_id}`}
        className="text-sm font-medium text-brand-700 hover:underline"
      >
        ← Back to job
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-slate-500">Application workspace</p>
          <h1 className="text-2xl font-semibold text-slate-900">{job.title}</h1>
          <p className="mt-1 text-slate-600">
            {job.company_name}
            {job.location ? ` · ${job.location}` : ""}
          </p>
        </div>
        <span
          className="rounded-full bg-brand-50 px-3 py-1 text-sm font-medium text-brand-700"
          aria-label="Application status"
        >
          {APPLICATION_STATUS_LABELS[application.status]}
        </span>
      </div>

      <Alert variant="info">
        This is your private preparation workspace. Starting an application does not submit it:
        nothing here is sent to {job.company_name}.
      </Alert>

      <nav aria-label="Workspace sections" className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {WORKSPACE_SECTIONS.map(([label, target]) => (
          <a key={target} href={`#${target}`} className="font-medium text-brand-700 hover:underline">
            {label}
          </a>
        ))}
      </nav>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="flex flex-col gap-6 lg:col-span-2">
          <JobCard application={application} />

          <form
            onSubmit={handleSubmit}
            className="flex flex-col gap-6"
            noValidate
            aria-label="Application preparation"
          >
            <Card aria-labelledby="preparation-heading">
              <h2 id="preparation-heading" className="text-lg font-semibold text-slate-900">
                Application preparation
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                These values belong to this application only. Your profile and preferences are
                never changed from here.
              </p>

              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
                <Input
                  label="Salary expectation"
                  name="salary"
                  type="number"
                  inputMode="decimal"
                  min={0}
                  step="any"
                  value={form.salary}
                  error={fieldErrors.salary}
                  onChange={(event) => setField("salary", event.target.value)}
                />
                <Input
                  label="Currency"
                  name="currency"
                  maxLength={3}
                  placeholder="EUR"
                  value={form.currency}
                  error={fieldErrors.currency}
                  onChange={(event) => setField("currency", event.target.value.toUpperCase())}
                />
              </div>
              <p className="mt-1 text-xs text-slate-400">Annual gross.</p>

              <div className="mt-4 flex max-w-md flex-col gap-1">
                <label htmlFor={FIELD_IDS.mentionSalary} className="text-sm font-medium text-slate-700">
                  Mention salary in the cover letter (Anschreiben)
                </label>
                <select
                  id={FIELD_IDS.mentionSalary}
                  className={SELECT_CLASS}
                  value={form.mentionSalary}
                  onChange={(event) =>
                    setField("mentionSalary", event.target.value as ApplicationForm["mentionSalary"])
                  }
                  {...invalidProps("mentionSalary", fieldErrors)}
                >
                  <option value="">Not decided yet</option>
                  <option value="yes">Yes, mention it</option>
                  <option value="no">No, leave it out</option>
                </select>
                <FieldError field="mentionSalary" errors={fieldErrors} />
                <p className="text-xs text-slate-400">
                  Your decision: the Anschreiben only states your salary expectation if you choose
                  Yes, and nothing is chosen for you.
                </p>
              </div>

              <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div className="flex flex-col gap-1">
                  <Input
                    label="Available from"
                    name="available_from"
                    type="date"
                    value={form.availableFrom}
                    error={fieldErrors.availableFrom}
                    onChange={(event) => setField("availableFrom", event.target.value)}
                  />
                  <p className="text-xs text-slate-400">A date in the past means you are available now.</p>
                </div>
                <Input
                  label="Notice period"
                  name="notice_value"
                  type="number"
                  min={0}
                  step={1}
                  value={form.noticeValue}
                  error={fieldErrors.noticeValue}
                  onChange={(event) => setField("noticeValue", event.target.value)}
                />
                <div className="flex flex-col gap-1">
                  <label htmlFor={FIELD_IDS.noticeUnit} className="text-sm font-medium text-slate-700">
                    Notice period unit
                  </label>
                  <select
                    id={FIELD_IDS.noticeUnit}
                    className={SELECT_CLASS}
                    value={form.noticeUnit}
                    onChange={(event) =>
                      setField("noticeUnit", event.target.value as ApplicationForm["noticeUnit"])
                    }
                  >
                    {NOTICE_UNIT_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="flex flex-col gap-1">
                  <label htmlFor={FIELD_IDS.language} className="text-sm font-medium text-slate-700">
                    Application language
                  </label>
                  <select
                    id={FIELD_IDS.language}
                    className={SELECT_CLASS}
                    value={form.language}
                    onChange={(event) =>
                      setField("language", event.target.value as ApplicationForm["language"])
                    }
                    {...invalidProps("language", fieldErrors)}
                  >
                    <option value="">Select a language</option>
                    {APPLICATION_LANGUAGE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <FieldError field="language" errors={fieldErrors} />
                  <p className="text-xs text-slate-400">
                    You choose it: JobPilot never guesses it from the job.
                  </p>
                </div>
                <div className="flex flex-col gap-1">
                  <label
                    htmlFor={FIELD_IDS.workAuthorization}
                    className="text-sm font-medium text-slate-700"
                  >
                    Work authorization (optional)
                  </label>
                  <select
                    id={FIELD_IDS.workAuthorization}
                    className={SELECT_CLASS}
                    value={form.workAuthorization}
                    onChange={(event) =>
                      setField(
                        "workAuthorization",
                        event.target.value as ApplicationForm["workAuthorization"]
                      )
                    }
                    {...invalidProps("workAuthorization", fieldErrors)}
                  >
                    <option value="">Not specified</option>
                    {WORK_AUTHORIZATION_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <FieldError field="workAuthorization" errors={fieldErrors} />
                  <p className="text-xs text-slate-400">Not every application asks.</p>
                </div>
              </div>
            </Card>

            <Card aria-labelledby="resume-heading">
              <h2 id="resume-heading" className="text-lg font-semibold text-slate-900">
                Resume
              </h2>
              {application.resume ? (
                <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-700">
                  <span className="font-medium text-slate-900">Base resume</span>
                  <span>
                    Version {application.resume.version_number} · {application.resume.file_name}
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      application.resume.is_confirmed
                        ? "bg-emerald-50 text-emerald-700"
                        : "bg-amber-50 text-amber-800"
                    }`}
                  >
                    {application.resume.is_confirmed ? "Confirmed" : "Not confirmed yet"}
                  </span>
                </p>
              ) : (
                <p className="mt-2 text-sm text-slate-600">
                  No resume is selected for this application.{" "}
                  <Link href="/profile/resume" className="font-medium text-brand-700 underline">
                    Upload one
                  </Link>
                  .
                </p>
              )}
              {canChooseResume && (
                <div className="mt-4 flex max-w-md flex-col gap-1">
                  <label htmlFor={FIELD_IDS.resumeId} className="text-sm font-medium text-slate-700">
                    Resume version
                  </label>
                  <select
                    id={FIELD_IDS.resumeId}
                    className={SELECT_CLASS}
                    value={form.resumeId}
                    onChange={(event) => setField("resumeId", event.target.value)}
                    {...invalidProps("resumeId", fieldErrors)}
                  >
                    {!application.resume && <option value="">Select a resume</option>}
                    {resumes.map((resume) => (
                      <option key={resume.id} value={resume.id}>
                        Version {resume.version_number} — {resume.file_name}
                        {resume.status === "confirmed" ? " (confirmed)" : ""}
                      </option>
                    ))}
                  </select>
                  <FieldError field="resumeId" errors={fieldErrors} />
                </div>
              )}
              <p className="mt-3 text-xs text-slate-400">
                This application keeps the version it was started with: uploading a newer resume
                doesn&apos;t change it. Tailored documents are built from this version.
              </p>
            </Card>

            <Card aria-labelledby="notes-heading">
              <h2 id="notes-heading" className="text-lg font-semibold text-slate-900">
                Notes
              </h2>
              <label
                htmlFor={FIELD_IDS.notes}
                className="mt-2 block text-sm font-medium text-slate-700"
              >
                Private notes
              </label>
              <textarea
                id={FIELD_IDS.notes}
                name="notes"
                rows={5}
                maxLength={10000}
                className={`mt-1 w-full ${SELECT_CLASS}`}
                placeholder="Contact person, referral, what to mention or ask..."
                value={form.notes}
                onChange={(event) => setField("notes", event.target.value)}
                {...invalidProps("notes", fieldErrors)}
              />
              <FieldError field="notes" errors={fieldErrors} />
              <p className="mt-1 text-xs text-slate-400">
                Only you can see these. JobPilot never sends your notes to an employer.
              </p>
            </Card>

            <div className="flex flex-col gap-3">
              {saveError && <Alert variant="error">{saveError}</Alert>}
              {isSaved && <Alert variant="success">Application saved.</Alert>}
              <div className="flex items-center gap-3">
                <Button type="submit" isLoading={isSaving} disabled={!dirty}>
                  {isSaving ? "Saving..." : "Save changes"}
                </Button>
                {!dirty && !isSaving && (
                  <span className="text-xs text-slate-400">No unsaved changes</span>
                )}
              </div>
            </div>
          </form>

          <ApplicationDocuments
            applicationId={application.id}
            language={application.application_language}
            reloadKey={application.updated_at}
            hasUnsavedChanges={dirty}
          />

          <ApplicationQuestions applicationId={application.id} />
        </div>

        <aside className="flex flex-col gap-6">
          <ProgressCard progress={application.progress} createdAt={application.created_at} />
          <MatchCard match={application.match} jobId={application.job_id} />
        </aside>
      </div>
    </div>
  );
}

/**
 * The parts of an application workspace, in the order a user works through them:
 *   Job -> Match -> Preparation -> CV -> Anschreiben -> Questions -> Notes
 */
const WORKSPACE_SECTIONS: [string, string][] = [
  ["Job", "job-heading"],
  ["Match", "match-summary-heading"],
  ["Preparation", "preparation-heading"],
  ["CV", "documents-tailored_cv-heading"],
  ["Anschreiben", "documents-cover_letter-heading"],
  ["Questions", "questions-heading"],
  ["Notes", "notes-heading"],
];

/** The job as it was when the application was started, with its original links. */
function JobCard({ application }: { application: Application }) {
  const { job, source } = application.context;
  const originalUrl = safeExternalUrl(source.original_url);
  const applicationUrl = safeExternalUrl(source.application_url);
  const showApplicationUrl =
    applicationUrl !== null &&
    applicationUrl !== originalUrl &&
    applicationUrl !== safeExternalUrl(source.canonical_url);
  const salary = formatSalary(job) ?? job.salary_text;
  const sourceName = `${sourceLabel(source.source)}${
    source.source_company_key ? ` (${source.source_company_key})` : ""
  }`;

  return (
    <Card aria-labelledby="job-heading">
      <h2 id="job-heading" className="text-lg font-semibold text-slate-900">
        Job
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        As it was when you started this application
        {formatDate(application.context.captured_at)
          ? ` (${formatDate(application.context.captured_at)})`
          : ""}
        . The live posting may have changed since;{" "}
        <Link href={`/jobs/${application.job_id}`} className="text-brand-700 underline">
          see the current job
        </Link>
        .
      </p>
      {application.source_is_active === false && (
        <div className="mt-3">
          <Alert variant="info">
            This posting is no longer listed by its source, so it may be closed. Check the
            original posting before you invest more time.
          </Alert>
        </div>
      )}
      <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <Fact label="Job title" value={job.title} />
        <Fact label="Company" value={job.company_name} />
        <Fact label="Location" value={job.location} />
        <Fact label="Source" value={sourceName} />
        <Fact label="Salary in the posting" value={salary} />
        <Fact label="Work model" value={job.work_model ? WORK_MODEL_LABELS[job.work_model] : null} />
        <Fact
          label="Employment type"
          value={job.employment_type ? EMPLOYMENT_TYPE_LABELS[job.employment_type] : null}
        />
        <Fact label="Posted" value={formatDate(job.posted_at)} />
      </dl>
      <div className="mt-4 flex flex-wrap items-center gap-4">
        {originalUrl ? (
          <a
            href={originalUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            View original posting ↗
          </a>
        ) : (
          <span className="text-sm text-slate-500">The source gave no link to the posting.</span>
        )}
        {showApplicationUrl && (
          <a
            href={applicationUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm font-medium text-brand-700 underline"
          >
            Application page ↗
          </a>
        )}
      </div>
    </Card>
  );
}

function ProgressCard({
  progress,
  createdAt,
}: {
  progress: PreparationProgress;
  createdAt: string;
}) {
  const created = formatDate(createdAt);
  return (
    <Card aria-labelledby="progress-heading">
      <h2 id="progress-heading" className="text-lg font-semibold text-slate-900">
        Preparation progress
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Application created{created ? ` ${created}` : ""}.
      </p>
      <p className="mt-3 text-sm font-medium text-slate-900" aria-label="Progress summary">
        {progress.completed} of {progress.total} steps complete
      </p>
      <div
        className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100"
        role="progressbar"
        aria-label="Preparation progress"
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={progress.completed}
      >
        <div
          className="h-full rounded-full bg-brand-600"
          style={{ width: `${progress.total ? (progress.completed / progress.total) * 100 : 0}%` }}
        />
      </div>
      <ul className="mt-4 flex flex-col gap-2 text-sm" aria-label="Preparation steps">
        {progress.items.map((item) => (
          <li
            key={item.key}
            className="flex items-start gap-2"
            data-complete={item.complete}
            data-required={item.required}
          >
            <span
              className={item.complete ? "text-emerald-600" : "text-slate-400"}
              aria-hidden="true"
            >
              {item.complete ? "✓" : "○"}
            </span>
            <span className={item.complete ? "text-slate-900" : "text-slate-600"}>
              {PREPARATION_LABELS[item.key]}
              <span className="sr-only">{item.complete ? " — done" : " — to do"}</span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Your match for the job, as the V0.6 matching last calculated it. Read-only. */
function MatchCard({ match, jobId }: { match: MatchSummary | null; jobId: string }) {
  return (
    <Card aria-labelledby="match-summary-heading">
      <h2 id="match-summary-heading" className="text-lg font-semibold text-slate-900">
        Your match
      </h2>
      {match ? (
        <div className="mt-3 flex flex-col gap-3">
          {match.status === "stale" && <Alert variant="info">{staleText(match.stale_reasons)}</Alert>}
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <p className="text-3xl font-semibold text-slate-900" aria-label="Overall match">
                {percent(match.overall_match)}
              </p>
              <p className="text-xs text-slate-500">Overall match</p>
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-slate-500">Job match</dt>
              <dd className="font-medium text-slate-900" aria-label="Job match">
                {percent(match.job_match)}
              </dd>
              <dt className="text-slate-500">Preference match</dt>
              <dd className="font-medium text-slate-900" aria-label="Preference match">
                {percent(match.preference_match)}
              </dd>
            </dl>
          </div>
          <span
            className={`w-fit rounded-full px-3 py-1 text-sm font-medium ${RECOMMENDATION_STYLES[match.recommendation]}`}
            aria-label="Recommendation"
          >
            {match.recommendation_label}
          </span>
        </div>
      ) : (
        <p className="mt-2 text-sm text-slate-600">Match not calculated yet.</p>
      )}
      <p className="mt-3 text-xs text-slate-500">
        <Link href={`/jobs/${jobId}`} className="text-brand-700 underline">
          {match ? "See why this job matches" : "Calculate it on the job page"}
        </Link>
        . The workspace never changes your match.
      </p>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-slate-500">{label}</dt>
      <dd className={value ? "text-slate-900" : "text-slate-400"}>{value ?? NOT_STATED}</dd>
    </div>
  );
}
