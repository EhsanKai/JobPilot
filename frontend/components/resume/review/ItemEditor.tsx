"use client";

import { FormEvent, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  ADDITIONAL_CATEGORY_LABELS,
  PERSONAL_FIELD_LABELS,
  SKILL_CATEGORY_LABELS,
} from "@/lib/resumeIntelligence";
import type { PersonalInfoItem, ReviewableItem, SectionName } from "@/types/resumeProfile";

type FieldType = "text" | "textarea" | "lines" | "commaList" | "checkbox" | "select";

interface FieldDef {
  name: string;
  label: string;
  type: FieldType;
  options?: { value: string; label: string }[];
  placeholder?: string;
}

const DATE_HINT = "YYYY or YYYY-MM";
const toOptions = (labels: Record<string, string>) =>
  Object.entries(labels).map(([value, label]) => ({ value, label }));

const FIELDS: Record<SectionName, FieldDef[]> = {
  personal_information: [{ name: "value", label: "Value", type: "text" }],
  summary: [{ name: "value", label: "Summary", type: "textarea" }],
  work_experience: [
    { name: "job_title", label: "Job title", type: "text" },
    { name: "company", label: "Company", type: "text" },
    { name: "location", label: "Location", type: "text" },
    { name: "start_date", label: "Start date", type: "text", placeholder: DATE_HINT },
    { name: "end_date", label: "End date", type: "text", placeholder: DATE_HINT },
    { name: "is_current", label: "I currently work here", type: "checkbox" },
    { name: "description", label: "Description", type: "textarea" },
    { name: "responsibilities", label: "Responsibilities (one per line)", type: "lines" },
    { name: "technologies", label: "Technologies (comma-separated)", type: "commaList" },
    { name: "achievements", label: "Achievements (one per line)", type: "lines" },
  ],
  education: [
    { name: "degree", label: "Degree", type: "text" },
    { name: "field_of_study", label: "Field of study", type: "text" },
    { name: "institution", label: "Institution", type: "text" },
    { name: "location", label: "Location", type: "text" },
    { name: "start_date", label: "Start date", type: "text", placeholder: DATE_HINT },
    { name: "end_date", label: "End date", type: "text", placeholder: DATE_HINT },
    { name: "description", label: "Description", type: "textarea" },
  ],
  skills: [
    { name: "name", label: "Skill", type: "text" },
    {
      name: "category",
      label: "Category",
      type: "select",
      options: toOptions(SKILL_CATEGORY_LABELS),
    },
    {
      name: "proficiency",
      label: "Proficiency",
      type: "select",
      options: [
        { value: "unknown", label: "Unknown" },
        { value: "beginner", label: "Beginner" },
        { value: "intermediate", label: "Intermediate" },
        { value: "advanced", label: "Advanced" },
        { value: "expert", label: "Expert" },
      ],
    },
  ],
  languages: [
    { name: "language", label: "Language", type: "text" },
    { name: "proficiency", label: "Proficiency (as written)", type: "text" },
    {
      name: "cefr_level",
      label: "CEFR level",
      type: "select",
      options: ["A1", "A2", "B1", "B2", "C1", "C2"].map((v) => ({ value: v, label: v })),
    },
  ],
  certifications: [
    { name: "name", label: "Name", type: "text" },
    { name: "issuer", label: "Issuer", type: "text" },
    { name: "date", label: "Date", type: "text", placeholder: DATE_HINT },
    { name: "credential_id", label: "Credential ID", type: "text" },
    { name: "url", label: "URL", type: "text" },
  ],
  projects: [
    { name: "name", label: "Name", type: "text" },
    { name: "role", label: "Role", type: "text" },
    { name: "description", label: "Description", type: "textarea" },
    { name: "technologies", label: "Technologies (comma-separated)", type: "commaList" },
    { name: "start_date", label: "Start date", type: "text", placeholder: DATE_HINT },
    { name: "end_date", label: "End date", type: "text", placeholder: DATE_HINT },
    { name: "url", label: "URL", type: "text" },
  ],
  additional_information: [
    {
      name: "category",
      label: "Category",
      type: "select",
      options: toOptions(ADDITIONAL_CATEGORY_LABELS),
    },
    { name: "value", label: "Details", type: "textarea" },
  ],
};

type FormValues = Record<string, string | boolean>;

function toFormValue(def: FieldDef, value: unknown): string | boolean {
  if (def.type === "checkbox") return Boolean(value);
  if (def.type === "lines") return ((value as string[] | null) ?? []).join("\n");
  if (def.type === "commaList") return ((value as string[] | null) ?? []).join(", ");
  return value === null || value === undefined ? "" : String(value);
}

function fromFormValue(def: FieldDef, value: string | boolean): unknown {
  if (def.type === "checkbox") return value;
  const text = String(value);
  if (def.type === "lines") return text.split("\n").map((v) => v.trim()).filter(Boolean);
  if (def.type === "commaList") return text.split(",").map((v) => v.trim()).filter(Boolean);
  return text.trim() === "" ? null : text.trim();
}

interface ItemEditorProps {
  section: SectionName;
  item: ReviewableItem;
  isSaving: boolean;
  onSave: (changes: Record<string, unknown>) => void;
  onCancel: () => void;
}

export function ItemEditor({ section, item, isSaving, onSave, onCancel }: ItemEditorProps) {
  const fields = FIELDS[section];
  const record = item as unknown as Record<string, unknown>;
  const [values, setValues] = useState<FormValues>(() =>
    Object.fromEntries(fields.map((def) => [def.name, toFormValue(def, record[def.name])]))
  );

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const changes: Record<string, unknown> = {};
    for (const def of fields) {
      const next = fromFormValue(def, values[def.name] ?? "");
      const previous = fromFormValue(def, toFormValue(def, record[def.name]));
      if (JSON.stringify(next) !== JSON.stringify(previous)) changes[def.name] = next;
    }
    if (Object.keys(changes).length === 0) {
      onCancel();
      return;
    }
    onSave(changes);
  }

  const idPrefix = `edit-${item.id}`;
  const heading =
    section === "personal_information"
      ? PERSONAL_FIELD_LABELS[(item as PersonalInfoItem).field]
      : null;

  return (
    <form onSubmit={handleSubmit} className="mt-3 flex flex-col gap-3" aria-label="Edit item">
      {heading && <p className="text-sm font-medium text-slate-700">{heading}</p>}
      {fields.map((def) => {
        const id = `${idPrefix}-${def.name}`;
        const value = values[def.name];
        const set = (v: string | boolean) => setValues((prev) => ({ ...prev, [def.name]: v }));
        const inputClass =
          "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500";
        if (def.type === "checkbox") {
          return (
            <label key={def.name} htmlFor={id} className="flex items-center gap-2 text-sm">
              <input
                id={id}
                type="checkbox"
                checked={Boolean(value)}
                onChange={(e) => set(e.target.checked)}
              />
              {def.label}
            </label>
          );
        }
        return (
          <div key={def.name} className="flex flex-col gap-1">
            <label htmlFor={id} className="text-sm font-medium text-slate-700">
              {def.label}
            </label>
            {def.type === "select" ? (
              <select
                id={id}
                value={String(value)}
                onChange={(e) => set(e.target.value)}
                className={inputClass}
              >
                {def.name !== "proficiency" && <option value="">Not specified</option>}
                {def.options!.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            ) : def.type === "textarea" || def.type === "lines" ? (
              <textarea
                id={id}
                rows={def.type === "lines" ? 4 : 3}
                value={String(value)}
                onChange={(e) => set(e.target.value)}
                className={inputClass}
              />
            ) : (
              <input
                id={id}
                type="text"
                value={String(value)}
                placeholder={def.placeholder}
                onChange={(e) => set(e.target.value)}
                className={inputClass}
              />
            )}
          </div>
        );
      })}
      <div className="flex gap-2">
        <Button type="submit" isLoading={isSaving}>
          Save changes
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
