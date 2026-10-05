"use client";

import { FormEvent, useState } from "react";

import { addJobTitle, deleteJobTitle } from "@/lib/api/preferences";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { DesiredJobTitle, JobTitlePriority } from "@/types/preferences";

interface DesiredRolesManagerProps {
  jobTitles: DesiredJobTitle[];
  onChange: (jobTitles: DesiredJobTitle[]) => void;
}

export function DesiredRolesManager({ jobTitles, onChange }: DesiredRolesManagerProps) {
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState<JobTitlePriority>("secondary");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!title.trim()) return;

    setError(null);
    setIsSubmitting(true);
    try {
      const created = await addJobTitle({ title: title.trim(), priority });
      onChange([...jobTitles, created]);
      setTitle("");
      setPriority("secondary");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add desired job title.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleDelete(jobTitleId: string) {
    setError(null);
    try {
      await deleteJobTitle(jobTitleId);
      onChange(jobTitles.filter((jt) => jt.id !== jobTitleId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to remove job title.");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <Alert variant="error">{error}</Alert>}

      {jobTitles.length === 0 ? (
        <p className="text-sm text-slate-500">No desired job titles added yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {jobTitles.map((jobTitle) => (
            <li
              key={jobTitle.id}
              className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-4 py-2"
            >
              <span className="font-medium text-slate-800">
                {jobTitle.title}
                {jobTitle.priority === "primary" && (
                  <span className="ml-2 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700">
                    Primary
                  </span>
                )}
              </span>
              <Button variant="danger" onClick={() => handleDelete(jobTitle.id)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-3" noValidate>
        <Input
          label="Add a desired job title"
          name="job_title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. SAP ABAP Developer"
        />
        <div className="flex flex-col gap-1">
          <label htmlFor="job_title_priority" className="text-sm font-medium text-slate-700">
            Priority
          </label>
          <select
            id="job_title_priority"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900"
            value={priority}
            onChange={(e) => setPriority(e.target.value as JobTitlePriority)}
          >
            <option value="primary">Primary</option>
            <option value="secondary">Secondary</option>
          </select>
        </div>
        <Button type="submit" isLoading={isSubmitting} disabled={!title.trim()}>
          {isSubmitting ? "Adding..." : "Add title"}
        </Button>
      </form>
    </div>
  );
}
