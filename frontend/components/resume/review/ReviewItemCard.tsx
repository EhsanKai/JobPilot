"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { ConfidenceBadge } from "@/components/resume/review/ConfidenceBadge";
import { EvidencePanel } from "@/components/resume/review/EvidencePanel";
import { ItemEditor } from "@/components/resume/review/ItemEditor";
import {
  ADDITIONAL_CATEGORY_LABELS,
  PERSONAL_FIELD_LABELS,
  SKILL_CATEGORY_LABELS,
  formatDateRange,
} from "@/lib/resumeIntelligence";
import type {
  AdditionalInfoItem,
  CertificationItem,
  EducationItem,
  Evidence,
  LanguageItem,
  PersonalInfoItem,
  ProjectItem,
  ReviewAction,
  ReviewableItem,
  SectionName,
  SkillItem,
  SummaryItem,
  WorkExperienceItem,
} from "@/types/resumeProfile";

function Chips({ values }: { values: string[] }) {
  if (values.length === 0) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-1.5">
      {values.map((value) => (
        <li key={value} className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-700">
          {value}
        </li>
      ))}
    </ul>
  );
}

function Bullets({ values, label }: { values: string[]; label?: string }) {
  if (values.length === 0) return null;
  return (
    <div className="mt-2">
      {label && <p className="text-xs font-medium uppercase text-slate-400">{label}</p>}
      <ul className="list-disc pl-5 text-sm text-slate-700">
        {values.map((value) => (
          <li key={value}>{value}</li>
        ))}
      </ul>
    </div>
  );
}

function Meta({ parts }: { parts: (string | null | undefined)[] }) {
  const text = parts.filter(Boolean).join(" · ");
  return text ? <p className="text-sm text-slate-500">{text}</p> : null;
}

function ItemContent({ section, item }: { section: SectionName; item: ReviewableItem }) {
  switch (section) {
    case "personal_information": {
      const p = item as PersonalInfoItem;
      return (
        <p className="text-sm text-slate-900">
          <span className="font-medium">{PERSONAL_FIELD_LABELS[p.field]}:</span> {p.value}
        </p>
      );
    }
    case "summary":
      return <p className="whitespace-pre-line text-sm text-slate-700">{(item as SummaryItem).value}</p>;
    case "work_experience": {
      const w = item as WorkExperienceItem;
      return (
        <div>
          <p className="font-medium text-slate-900">
            {w.job_title ?? "Unknown title"}
            {w.company ? ` — ${w.company}` : ""}
          </p>
          <Meta parts={[w.location, formatDateRange(w.start_date, w.end_date, w.is_current)]} />
          {w.description && <p className="mt-2 text-sm text-slate-700">{w.description}</p>}
          <Bullets values={w.responsibilities} />
          <Bullets values={w.achievements} label="Achievements" />
          <Chips values={w.technologies} />
        </div>
      );
    }
    case "education": {
      const e = item as EducationItem;
      return (
        <div>
          <p className="font-medium text-slate-900">
            {[e.degree, e.field_of_study].filter(Boolean).join(" ") || "Unknown degree"}
          </p>
          <Meta parts={[e.institution, e.location, formatDateRange(e.start_date, e.end_date)]} />
          {e.description && <p className="mt-2 text-sm text-slate-700">{e.description}</p>}
        </div>
      );
    }
    case "skills": {
      const s = item as SkillItem;
      return (
        <div>
          <p className="font-medium text-slate-900">{s.name}</p>
          <Meta
            parts={[
              s.category ? SKILL_CATEGORY_LABELS[s.category] : null,
              s.proficiency !== "unknown" ? `Stated level: ${s.proficiency}` : "Level: unknown",
              s.aliases.length > 0 ? `Written as: ${s.aliases.join(", ")}` : null,
            ]}
          />
        </div>
      );
    }
    case "languages": {
      const l = item as LanguageItem;
      return (
        <div>
          <p className="font-medium text-slate-900">{l.language}</p>
          <Meta
            parts={[
              l.proficiency ? `Stated: ${l.proficiency}` : "Proficiency not stated",
              l.cefr_level ? `CEFR ${l.cefr_level}` : null,
            ]}
          />
        </div>
      );
    }
    case "certifications": {
      const c = item as CertificationItem;
      return (
        <div>
          <p className="font-medium text-slate-900">{c.name}</p>
          <Meta parts={[c.issuer, c.date, c.credential_id ? `ID ${c.credential_id}` : null]} />
        </div>
      );
    }
    case "projects": {
      const p = item as ProjectItem;
      return (
        <div>
          <p className="font-medium text-slate-900">{p.name}</p>
          <Meta parts={[p.role, formatDateRange(p.start_date, p.end_date)]} />
          {p.description && <p className="mt-2 text-sm text-slate-700">{p.description}</p>}
          <Chips values={p.technologies} />
        </div>
      );
    }
    case "additional_information": {
      const a = item as AdditionalInfoItem;
      const label = ADDITIONAL_CATEGORY_LABELS[a.category];
      // Values are kept as written, which often already include the label
      // ("Driving license: Class B") -- don't repeat it.
      const repeatsLabel = a.value.toLowerCase().startsWith(label.toLowerCase());
      return (
        <p className="text-sm text-slate-900">
          {!repeatsLabel && <span className="font-medium">{label}: </span>}
          {a.value}
        </p>
      );
    }
  }
}

const STATUS_BADGES: Record<string, { label: string; className: string } | undefined> = {
  accepted: { label: "Accepted", className: "bg-emerald-50 text-emerald-700" },
  edited: { label: "Edited by you", className: "bg-sky-50 text-sky-700" },
  rejected: { label: "Rejected", className: "bg-slate-100 text-slate-500" },
};

interface ReviewItemCardProps {
  section: SectionName;
  item: ReviewableItem;
  readOnly: boolean;
  isBusy: boolean;
  onAction: (action: ReviewAction, changes?: Record<string, unknown>) => Promise<boolean>;
  loadSource: () => Promise<string>;
}

export function ReviewItemCard({
  section,
  item,
  readOnly,
  isBusy,
  onAction,
  loadSource,
}: ReviewItemCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);
  const rejected = item.review_status === "rejected";
  const badge = STATUS_BADGES[item.review_status];
  const evidence: Evidence[] =
    section === "skills" && (item as SkillItem).proficiency_evidence
      ? [...item.evidence, (item as SkillItem).proficiency_evidence as Evidence]
      : item.evidence;

  async function save(changes: Record<string, unknown>) {
    if (await onAction("edit", changes)) setIsEditing(false);
  }

  return (
    <li
      className={`rounded-lg border p-4 ${rejected ? "border-dashed border-slate-200 bg-slate-50 opacity-70" : "border-slate-200 bg-white"}`}
      data-testid={`review-item-${item.id}`}
      aria-label={`${section} item`}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className={rejected ? "line-through" : ""}>
          <ItemContent section={section} item={item} />
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {badge && (
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}>
              {badge.label}
            </span>
          )}
          {item.basis === "inferred" && (
            <span
              className="rounded-full bg-violet-50 px-2 py-0.5 text-xs text-violet-700"
              title="Derived by JobPilot rather than stated word-for-word"
            >
              Inferred
            </span>
          )}
          <ConfidenceBadge confidence={item.confidence} />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="text-xs font-medium text-brand-700 underline"
          onClick={() => setShowEvidence((v) => !v)}
          aria-expanded={showEvidence}
        >
          {showEvidence ? "Hide evidence" : "Why?"}
        </button>
        {!readOnly && !isEditing && (
          <div className="ml-auto flex flex-wrap gap-2">
            {item.review_status === "pending" ? (
              <>
                <Button variant="secondary" onClick={() => void onAction("accept")} disabled={isBusy}>
                  Accept
                </Button>
                <Button variant="secondary" onClick={() => setIsEditing(true)} disabled={isBusy}>
                  Edit
                </Button>
                <Button variant="secondary" onClick={() => void onAction("reject")} disabled={isBusy}>
                  Reject
                </Button>
              </>
            ) : (
              <>
                {!rejected && (
                  <Button variant="secondary" onClick={() => setIsEditing(true)} disabled={isBusy}>
                    Edit
                  </Button>
                )}
                <Button variant="secondary" onClick={() => void onAction("reset")} disabled={isBusy}>
                  {item.review_status === "edited" ? "Restore original" : "Undo"}
                </Button>
              </>
            )}
          </div>
        )}
      </div>

      {showEvidence && (
        <div className="mt-3 rounded-md bg-slate-50 p-3">
          <EvidencePanel evidence={evidence} loadSource={loadSource} />
        </div>
      )}

      {isEditing && (
        <ItemEditor
          section={section}
          item={item}
          isSaving={isBusy}
          onSave={(changes) => void save(changes)}
          onCancel={() => setIsEditing(false)}
        />
      )}
    </li>
  );
}
