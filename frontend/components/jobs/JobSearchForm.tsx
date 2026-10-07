"use client";

import { FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { EMPLOYMENT_TYPE_LABELS, WORK_MODEL_LABELS } from "@/lib/jobs";
import type { JobSearchValues } from "@/lib/jobSearch";
import type { JobSource } from "@/types/jobs";
import type { EmploymentType, RemotePreference } from "@/types/preferences";

interface JobSearchFormProps {
  values: JobSearchValues;
  sources: JobSource[];
  isDiscovering: boolean;
  onChange: (values: JobSearchValues) => void;
  /** Filter jobs JobPilot already found. */
  onSearch: () => void;
  /** Look for new jobs at the sources using these filters. */
  onDiscover: () => void;
  /** Look for new jobs using the saved Job Preferences. */
  onDiscoverWithPreferences: () => void;
}

const SELECT_CLASS =
  "rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500";

const POSTED_WITHIN = [
  { value: "", label: "Any time" },
  { value: "1", label: "Last 24 hours" },
  { value: "7", label: "Last 7 days" },
  { value: "14", label: "Last 14 days" },
  { value: "30", label: "Last 30 days" },
];

export function JobSearchForm({
  values,
  sources,
  isDiscovering,
  onChange,
  onSearch,
  onDiscover,
  onDiscoverWithPreferences,
}: JobSearchFormProps) {
  function update<K extends keyof JobSearchValues>(key: K, value: JobSearchValues[K]) {
    onChange({ ...values, [key]: value });
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onSearch();
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4" aria-label="Job search">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Input
          label="Keywords"
          name="keyword"
          placeholder="e.g. Python, Product Designer"
          value={values.keyword}
          onChange={(e) => update("keyword", e.target.value)}
        />
        <Input
          label="Location"
          name="location"
          placeholder="e.g. Berlin"
          value={values.location}
          onChange={(e) => update("location", e.target.value)}
        />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Work model
          <select
            className={SELECT_CLASS}
            value={values.workModel}
            onChange={(e) => update("workModel", e.target.value as RemotePreference | "")}
          >
            <option value="">Any</option>
            {Object.entries(WORK_MODEL_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Employment type
          <select
            className={SELECT_CLASS}
            value={values.employmentType}
            onChange={(e) => update("employmentType", e.target.value as EmploymentType | "")}
          >
            <option value="">Any</option>
            {Object.entries(EMPLOYMENT_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Source
          <select
            className={SELECT_CLASS}
            value={values.source}
            onChange={(e) => update("source", e.target.value)}
          >
            <option value="">All sources</option>
            {sources
              .filter((source) => source.enabled)
              .map((source) => (
                <option key={source.name} value={source.name}>
                  {source.display_name}
                </option>
              ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Posted
          <select
            className={SELECT_CLASS}
            value={values.postedWithin}
            onChange={(e) => update("postedWithin", e.target.value)}
          >
            {POSTED_WITHIN.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="secondary">
          Filter my jobs
        </Button>
        <Button type="button" onClick={onDiscover} isLoading={isDiscovering}>
          Discover jobs
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={onDiscoverWithPreferences}
          disabled={isDiscovering}
        >
          Discover with my preferences
        </Button>
      </div>
      <p className="text-xs text-slate-500">
        &quot;Filter my jobs&quot; searches jobs JobPilot already found for you. &quot;Discover
        jobs&quot; asks the job sources for new postings. Separate several keywords or locations
        with commas.
      </p>
    </form>
  );
}
