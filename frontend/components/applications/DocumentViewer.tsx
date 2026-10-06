"use client";

import { describeDates, describeGenerated, languageName } from "@/lib/documents";
import type {
  CoverLetterContent,
  GeneratedDocument,
  SourceFact,
  TailoredCvContent,
} from "@/types/documents";

const SECTION_HEADING = "text-sm font-semibold uppercase tracking-wide text-slate-500";

/** The facts a piece of generated text is based on, so it can be checked against the resume. */
function Sources({ ids, facts }: { ids: string[]; facts: Record<string, SourceFact> }) {
  const known = ids.filter((id) => facts[id]);
  if (known.length === 0) return null;
  return (
    <details className="mt-1 text-xs text-slate-500">
      <summary className="cursor-pointer select-none hover:text-slate-700">
        Sources ({known.length})
      </summary>
      <ul className="mt-1 list-disc space-y-0.5 pl-5">
        {known.map((id) => (
          <li key={id}>{facts[id]?.text}</li>
        ))}
      </ul>
    </details>
  );
}

function CvView({
  content,
  facts,
}: {
  content: TailoredCvContent;
  facts: Record<string, SourceFact>;
}) {
  const { header } = content;
  const contact = [header.email, header.phone, header.location, ...header.links].filter(Boolean);
  const emphasized = content.skills.filter((skill) => skill.emphasized);
  return (
    <div className="flex flex-col gap-6 text-sm text-slate-800">
      <header>
        <h4 className="text-xl font-semibold text-slate-900">{header.full_name ?? "Your name"}</h4>
        {contact.length > 0 && <p className="mt-1 text-slate-600">{contact.join(" · ")}</p>}
      </header>

      <section aria-label="Summary">
        <h5 className={SECTION_HEADING}>Summary</h5>
        <p className="mt-1">{content.summary.text}</p>
        <Sources ids={content.summary.source_fact_ids} facts={facts} />
      </section>

      {content.experience.length > 0 && (
        <section aria-label="Experience">
          <h5 className={SECTION_HEADING}>Experience</h5>
          <div className="mt-2 flex flex-col gap-4">
            {content.experience.map((entry) => {
              const dates = describeDates(entry);
              return (
                <div key={entry.source_fact_id}>
                  <p className="font-medium text-slate-900">
                    {[entry.job_title, entry.company].filter(Boolean).join(" — ")}
                  </p>
                  <p className="text-slate-500">
                    {[entry.location, dates].filter(Boolean).join(" · ")}
                  </p>
                  {entry.bullets.length > 0 && (
                    <ul className="mt-1 flex list-disc flex-col gap-1.5 pl-5">
                      {entry.bullets.map((bullet, index) => (
                        <li key={index}>
                          {bullet.text}
                          <Sources ids={bullet.source_fact_ids} facts={facts} />
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {content.skills.length > 0 && (
        <section aria-label="Skills">
          <h5 className={SECTION_HEADING}>Skills</h5>
          <ul className="mt-1 flex flex-wrap gap-2">
            {content.skills.map((skill) => (
              <li
                key={skill.source_fact_id}
                data-emphasized={skill.emphasized}
                className={
                  skill.emphasized
                    ? "rounded-full bg-brand-50 px-3 py-1 font-semibold text-brand-700"
                    : "rounded-full bg-slate-100 px-3 py-1 text-slate-700"
                }
              >
                {skill.name}
              </li>
            ))}
          </ul>
          {emphasized.length > 0 && (
            <p className="mt-1 text-xs text-slate-500">
              Highlighted skills are the ones most relevant to this job. Nothing was added to your
              skills.
            </p>
          )}
        </section>
      )}

      {content.education.length > 0 && (
        <section aria-label="Education">
          <h5 className={SECTION_HEADING}>Education</h5>
          <ul className="mt-1 flex flex-col gap-1">
            {content.education.map((entry) => (
              <li key={entry.source_fact_id}>
                {[entry.degree, entry.field_of_study, entry.institution]
                  .filter(Boolean)
                  .join(", ")}
                {describeDates(entry) ? ` (${describeDates(entry)})` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {content.languages.length > 0 && (
        <section aria-label="Languages">
          <h5 className={SECTION_HEADING}>Languages</h5>
          <ul className="mt-1 flex flex-col gap-1">
            {content.languages.map((entry) => (
              <li key={entry.source_fact_id}>
                {entry.language}
                {entry.proficiency ? ` — ${entry.proficiency}` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {content.certifications.length > 0 && (
        <section aria-label="Certifications">
          <h5 className={SECTION_HEADING}>Certifications</h5>
          <ul className="mt-1 flex flex-col gap-1">
            {content.certifications.map((entry) => (
              <li key={entry.source_fact_id}>
                {[entry.name, entry.issuer, entry.date].filter(Boolean).join(", ")}
              </li>
            ))}
          </ul>
        </section>
      )}

      {content.projects.length > 0 && (
        <section aria-label="Projects">
          <h5 className={SECTION_HEADING}>Projects</h5>
          <ul className="mt-1 flex flex-col gap-1.5">
            {content.projects.map((entry) => (
              <li key={entry.source_fact_id}>
                <span className="font-medium">{entry.name}</span>
                {entry.role ? ` (${entry.role})` : ""}
                {entry.description ? ` — ${entry.description}` : ""}
                {entry.technologies.length > 0 ? ` [${entry.technologies.join(", ")}]` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {content.additional_information.length > 0 && (
        <section aria-label="Additional information">
          <h5 className={SECTION_HEADING}>Additional information</h5>
          <ul className="mt-1 flex flex-col gap-1">
            {content.additional_information.map((entry) => (
              <li key={entry.source_fact_id}>{entry.value}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function LetterView({
  content,
  facts,
}: {
  content: CoverLetterContent;
  facts: Record<string, SourceFact>;
}) {
  const sender = [
    content.sender.full_name,
    content.sender.email,
    content.sender.phone,
    content.sender.location,
  ].filter(Boolean);
  return (
    <div className="flex flex-col gap-4 text-sm text-slate-800">
      {sender.length > 0 && (
        <p className="text-slate-600" aria-label="Sender">
          {sender.join(" · ")}
        </p>
      )}
      {content.recipient.company_name && (
        <p className="font-medium text-slate-900" aria-label="Recipient">
          {content.recipient.company_name}
        </p>
      )}
      <p className="font-semibold text-slate-900">{content.subject}</p>
      <p>{content.salutation}</p>
      {content.paragraphs.map((paragraph, index) => (
        <div key={index}>
          <p data-role={paragraph.role}>{paragraph.text}</p>
          <Sources ids={paragraph.source_fact_ids} facts={facts} />
        </div>
      ))}
      {content.salary_statement && (
        <p data-role="salary" aria-label="Salary statement">
          {content.salary_statement}
        </p>
      )}
      <div>
        <p>{content.closing_formula}</p>
        {content.signature && <p className="mt-4 font-medium text-slate-900">{content.signature}</p>}
      </div>
    </div>
  );
}

/**
 * A generated document, as it was validated: the text, where each statement
 * comes from, and how it was produced. This is the AI's original: nothing here
 * is edited, and an earlier version stays exactly as it was.
 */
export function DocumentViewer({ document, title }: { document: GeneratedDocument; title: string }) {
  const { content } = document;
  const generated = describeGenerated(document.completed_at ?? document.created_at);
  return (
    <section
      role="region"
      aria-label={`${title}, version ${document.version_number}`}
      className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4"
    >
      <dl className="mb-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-500">
        <div>
          <dt className="inline">Version </dt>
          <dd className="inline font-medium text-slate-700">{document.version_number}</dd>
        </div>
        <div>
          <dt className="inline">Written in </dt>
          <dd className="inline font-medium text-slate-700">{languageName(document.language)}</dd>
        </div>
        <div>
          <dt className="inline">Generated </dt>
          <dd className="inline font-medium text-slate-700">{generated}</dd>
        </div>
        {document.resume_version_number !== null && (
          <div>
            <dt className="inline">From resume version </dt>
            <dd className="inline font-medium text-slate-700">{document.resume_version_number}</dd>
          </div>
        )}
        <div>
          <dt className="inline">Validation </dt>
          <dd className="inline font-medium text-emerald-700">
            {document.validation_status === "passed"
              ? "Passed — every statement is backed by your resume"
              : document.validation_status}
          </dd>
        </div>
        <div>
          <dt className="inline">Produced by </dt>
          <dd className="inline font-medium text-slate-700">
            {document.ai_provider}
            {document.ai_model ? ` (${document.ai_model})` : ""} · {document.prompt_version}
          </dd>
        </div>
      </dl>
      <div className="rounded-lg bg-white p-5 shadow-sm">
        {content === null ? (
          <p className="text-sm text-slate-500">This document has no content.</p>
        ) : content.kind === "tailored_cv" ? (
          <CvView content={content} facts={document.source_facts} />
        ) : (
          <LetterView content={content} facts={document.source_facts} />
        )}
      </div>
    </section>
  );
}
