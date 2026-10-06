"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import type {
  CoverLetterContent,
  CvEditRequest,
  DocumentEditRequest,
  GeneratedDocument,
  LetterEditRequest,
  LetterRole,
  TailoredCvContent,
} from "@/types/documents";

const FIELD =
  "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500";
const LABEL = "text-sm font-medium text-slate-700";

const ROLE_LABELS: Record<LetterRole, string> = {
  opening: "Opening",
  role_fit: "Why this role",
  experience: "Experience",
  skills: "Skills",
  motivation: "Motivation",
  closing: "Closing",
};

interface Props {
  document: GeneratedDocument;
  title: string;
  isSaving: boolean;
  error: string | null;
  onSave: (edit: DocumentEditRequest) => void;
  onCancel: () => void;
}

/**
 * The user's own editor for a version. Saving creates a *new* version "edited by
 * you": the version being edited is never changed, no AI is involved, and the
 * words saved here are never presented as checked by JobPilot.
 */
export function DocumentEditor({ document, title, isSaving, error, onSave, onCancel }: Props) {
  const { content } = document;
  if (content === null) return null;
  return (
    <section
      role="region"
      aria-label={`Edit ${title}`}
      className="mt-4 rounded-lg border border-brand-200 bg-white p-4"
    >
      <p className="text-sm text-slate-600">
        You are editing version {document.version_number}. Saving creates a new version (
        <em>edited by you</em>); this version stays exactly as it is. What you write is your own:
        JobPilot does not check it, and the AI never overwrites it. Employers, titles, dates and
        skills come from your resume — change those in the resume review.
      </p>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
      {content.kind === "tailored_cv" ? (
        <CvFields content={content} isSaving={isSaving} onSave={onSave} onCancel={onCancel} />
      ) : (
        <LetterFields content={content} isSaving={isSaving} onSave={onSave} onCancel={onCancel} />
      )}
    </section>
  );
}

interface FieldsProps<T> {
  content: T;
  isSaving: boolean;
  onSave: (edit: DocumentEditRequest) => void;
  onCancel: () => void;
}

function blank(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

function CvFields({ content, isSaving, onSave, onCancel }: FieldsProps<TailoredCvContent>) {
  const [summary, setSummary] = useState(content.summary.text);
  const [name, setName] = useState(content.header.full_name ?? "");
  const [email, setEmail] = useState(content.header.email ?? "");
  const [phone, setPhone] = useState(content.header.phone ?? "");
  const [location, setLocation] = useState(content.header.location ?? "");
  const [links, setLinks] = useState(content.header.links.join("\n"));
  const [bullets, setBullets] = useState<Record<string, string[]>>(() =>
    Object.fromEntries(
      content.experience.map((entry) => [entry.source_fact_id, entry.bullets.map((b) => b.text)])
    )
  );

  function setBullet(id: string, index: number, value: string) {
    setBullets((current) => ({
      ...current,
      [id]: (current[id] ?? []).map((text, i) => (i === index ? value : text)),
    }));
  }

  function submit() {
    const edit: CvEditRequest = {
      kind: "tailored_cv",
      summary,
      header: {
        full_name: blank(name),
        email: blank(email),
        phone: blank(phone),
        location: blank(location),
        links: links
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      },
      experience: content.experience.map((entry) => ({
        source_fact_id: entry.source_fact_id,
        bullets: (bullets[entry.source_fact_id] ?? []).map((b) => b.trim()).filter(Boolean),
      })),
    };
    onSave(edit);
  }

  return (
    <div className="mt-3 flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="cv-edit-name" className={LABEL}>
            Name
          </label>
          <input id="cv-edit-name" className={FIELD} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label htmlFor="cv-edit-email" className={LABEL}>
            Email
          </label>
          <input id="cv-edit-email" className={FIELD} value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label htmlFor="cv-edit-phone" className={LABEL}>
            Phone
          </label>
          <input id="cv-edit-phone" className={FIELD} value={phone} onChange={(e) => setPhone(e.target.value)} />
        </div>
        <div>
          <label htmlFor="cv-edit-location" className={LABEL}>
            Location
          </label>
          <input
            id="cv-edit-location"
            className={FIELD}
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
        </div>
      </div>
      <div>
        <label htmlFor="cv-edit-links" className={LABEL}>
          Links (one per line)
        </label>
        <textarea id="cv-edit-links" rows={2} className={FIELD} value={links} onChange={(e) => setLinks(e.target.value)} />
      </div>
      <div>
        <label htmlFor="cv-edit-summary" className={LABEL}>
          Summary
        </label>
        <textarea
          id="cv-edit-summary"
          rows={4}
          className={FIELD}
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
        />
      </div>
      {content.experience.map((entry) => (
        <fieldset key={entry.source_fact_id} className="rounded-lg border border-slate-200 p-3">
          <legend className="px-1 text-sm font-semibold text-slate-800">
            {[entry.job_title, entry.company].filter(Boolean).join(" — ")}
          </legend>
          {(bullets[entry.source_fact_id] ?? []).map((text, index) => (
            <div key={index} className="mt-2 flex items-start gap-2">
              <textarea
                aria-label={`${entry.job_title ?? "Role"} bullet ${index + 1}`}
                rows={2}
                className={FIELD}
                value={text}
                onChange={(e) => setBullet(entry.source_fact_id, index, e.target.value)}
              />
              <Button
                variant="secondary"
                aria-label={`Remove bullet ${index + 1} of ${entry.job_title ?? "role"}`}
                onClick={() =>
                  setBullets((current) => ({
                    ...current,
                    [entry.source_fact_id]: (current[entry.source_fact_id] ?? []).filter((_, i) => i !== index),
                  }))
                }
              >
                Remove
              </Button>
            </div>
          ))}
          <div className="mt-2">
            <Button
              variant="secondary"
              aria-label={`Add bullet to ${entry.job_title ?? "role"}`}
              onClick={() =>
                setBullets((current) => ({
                  ...current,
                  [entry.source_fact_id]: [...(current[entry.source_fact_id] ?? []), ""],
                }))
              }
            >
              Add bullet
            </Button>
          </div>
        </fieldset>
      ))}
      <div className="flex gap-2">
        <Button isLoading={isSaving} disabled={summary.trim() === ""} onClick={submit}>
          Save as new version
        </Button>
        <Button variant="secondary" disabled={isSaving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function LetterFields({ content, isSaving, onSave, onCancel }: FieldsProps<CoverLetterContent>) {
  const [subject, setSubject] = useState(content.subject);
  const [salutation, setSalutation] = useState(content.salutation);
  const [paragraphs, setParagraphs] = useState(
    content.paragraphs.map((p) => ({ role: p.role, text: p.text }))
  );
  const [salary, setSalary] = useState(content.salary_statement ?? "");
  const [closing, setClosing] = useState(content.closing_formula);
  const [signature, setSignature] = useState(content.signature ?? "");

  function submit() {
    const edit: LetterEditRequest = {
      kind: "cover_letter",
      subject,
      salutation,
      paragraphs: paragraphs.filter((p) => p.text.trim() !== ""),
      salary_statement: blank(salary),
      closing_formula: closing,
      signature: blank(signature),
    };
    onSave(edit);
  }

  const valid =
    subject.trim() !== "" &&
    salutation.trim() !== "" &&
    closing.trim() !== "" &&
    paragraphs.some((p) => p.text.trim() !== "");

  return (
    <div className="mt-3 flex flex-col gap-3">
      <div>
        <label htmlFor="letter-edit-subject" className={LABEL}>
          Subject
        </label>
        <input
          id="letter-edit-subject"
          className={FIELD}
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="letter-edit-salutation" className={LABEL}>
          Salutation
        </label>
        <input
          id="letter-edit-salutation"
          className={FIELD}
          value={salutation}
          onChange={(e) => setSalutation(e.target.value)}
        />
      </div>
      {paragraphs.map((paragraph, index) => (
        <div key={index} className="flex items-start gap-2">
          <div className="w-full">
            <label htmlFor={`letter-edit-p${index}`} className={LABEL}>
              Paragraph {index + 1} · {ROLE_LABELS[paragraph.role]}
            </label>
            <textarea
              id={`letter-edit-p${index}`}
              rows={4}
              className={FIELD}
              value={paragraph.text}
              onChange={(e) =>
                setParagraphs((current) =>
                  current.map((p, i) => (i === index ? { ...p, text: e.target.value } : p))
                )
              }
            />
          </div>
          <Button
            variant="secondary"
            className="mt-6"
            aria-label={`Remove paragraph ${index + 1}`}
            disabled={paragraphs.length <= 1}
            onClick={() => setParagraphs((current) => current.filter((_, i) => i !== index))}
          >
            Remove
          </Button>
        </div>
      ))}
      <div>
        <Button
          variant="secondary"
          onClick={() => setParagraphs((current) => [...current, { role: "experience", text: "" }])}
        >
          Add paragraph
        </Button>
      </div>
      <div>
        <label htmlFor="letter-edit-salary" className={LABEL}>
          Salary sentence
        </label>
        <textarea
          id="letter-edit-salary"
          rows={2}
          className={FIELD}
          value={salary}
          onChange={(e) => setSalary(e.target.value)}
        />
        <p className="mt-1 text-xs text-slate-500">
          Leave empty to leave your salary expectation out of this version.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="letter-edit-closing" className={LABEL}>
            Closing formula
          </label>
          <input
            id="letter-edit-closing"
            className={FIELD}
            value={closing}
            onChange={(e) => setClosing(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="letter-edit-signature" className={LABEL}>
            Signature
          </label>
          <input
            id="letter-edit-signature"
            className={FIELD}
            value={signature}
            onChange={(e) => setSignature(e.target.value)}
          />
        </div>
      </div>
      <div className="flex gap-2">
        <Button isLoading={isSaving} disabled={!valid} onClick={submit}>
          Save as new version
        </Button>
        <Button variant="secondary" disabled={isSaving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
