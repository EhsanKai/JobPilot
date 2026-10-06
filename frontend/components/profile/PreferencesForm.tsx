"use client";

import { FormEvent, useState } from "react";

import { updatePreferences } from "@/lib/api/preferences";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { DealBreakerType, JobPreferences } from "@/types/preferences";

interface PreferencesFormProps {
  preferences: JobPreferences;
  onSaved: (updated: JobPreferences) => void;
}

const EMPLOYMENT_TYPES: JobPreferences["employment_types"] = [
  "full_time",
  "part_time",
  "contract",
  "temporary",
  "internship",
  "working_student",
  "freelance",
];

const WORK_MODELS: JobPreferences["work_models"] = ["remote", "hybrid", "onsite", "flexible"];

const DEAL_BREAKERS: { type: DealBreakerType; label: string }[] = [
  { type: "minimum_salary", label: "Salary below minimum" },
  { type: "remote_requirement", label: "Fully on-site" },
  { type: "relocation", label: "Required relocation" },
  { type: "travel", label: "Frequent travel" },
  { type: "employment_type", label: "Specific employment types" },
  { type: "location", label: "Outside preferred locations" },
];

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function dealBreakerState(preferences: JobPreferences): Record<DealBreakerType, boolean> {
  const state = {} as Record<DealBreakerType, boolean>;
  for (const { type } of DEAL_BREAKERS) {
    state[type] = preferences.deal_breakers.some((db) => db.type === type && db.enabled);
  }
  return state;
}

export function PreferencesForm({ preferences, onSaved }: PreferencesFormProps) {
  const [employmentTypes, setEmploymentTypes] = useState<Set<string>>(
    new Set(preferences.employment_types)
  );
  const [workModels, setWorkModels] = useState<Set<string>>(new Set(preferences.work_models));
  const [preferredIndustries, setPreferredIndustries] = useState(
    preferences.preferred_industries.join(", ")
  );
  const [minimumSalary, setMinimumSalary] = useState(preferences.minimum_salary?.toString() ?? "");
  const [desiredSalary, setDesiredSalary] = useState(preferences.desired_salary?.toString() ?? "");
  const [salaryCurrency, setSalaryCurrency] = useState(preferences.salary_currency ?? "");
  const [relocation, setRelocation] = useState(preferences.relocation ?? "");
  const [travelWillingness, setTravelWillingness] = useState(preferences.travel_willingness ?? "");
  const [dealBreakers, setDealBreakers] = useState<Record<DealBreakerType, boolean>>(
    dealBreakerState(preferences)
  );

  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  function toggleInSet(set: Set<string>, setSet: (next: Set<string>) => void, value: string) {
    const next = new Set(set);
    if (next.has(value)) {
      next.delete(value);
    } else {
      next.add(value);
    }
    setSet(next);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccessMessage(null);
    setIsSaving(true);

    try {
      const updated = await updatePreferences({
        // Desired job titles and preferred locations are managed by their
        // own sections via dedicated endpoints; passed through unchanged
        // here so this save doesn't wipe them out.
        desired_job_titles: preferences.desired_job_titles.map((jt) => ({
          title: jt.title,
          priority: jt.priority,
        })),
        preferred_locations: preferences.preferred_locations.map((loc) => ({
          location: loc.location,
          radius_km: loc.radius_km,
        })),
        employment_types: Array.from(employmentTypes) as JobPreferences["employment_types"],
        preferred_industries: splitList(preferredIndustries),
        work_models: Array.from(workModels) as JobPreferences["work_models"],
        minimum_salary: minimumSalary ? Number(minimumSalary) : null,
        desired_salary: desiredSalary ? Number(desiredSalary) : null,
        salary_currency: salaryCurrency || null,
        relocation: (relocation || null) as JobPreferences["relocation"],
        travel_willingness: (travelWillingness || null) as JobPreferences["travel_willingness"],
        deal_breakers: DEAL_BREAKERS.map(({ type }) => ({ type, enabled: dealBreakers[type] })),
      });
      onSaved(updated);
      setSuccessMessage("Preferences saved successfully.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save your preferences.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6" noValidate>
      {error && <Alert variant="error">{error}</Alert>}
      {successMessage && <Alert variant="success">{successMessage}</Alert>}

      <section className="flex flex-col gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Salary</h3>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Input
            label="Minimum salary"
            name="minimum_salary"
            type="number"
            min={0}
            value={minimumSalary}
            onChange={(e) => setMinimumSalary(e.target.value)}
          />
          <Input
            label="Desired salary"
            name="desired_salary"
            type="number"
            min={0}
            value={desiredSalary}
            onChange={(e) => setDesiredSalary(e.target.value)}
          />
          <Input
            label="Currency"
            name="salary_currency"
            maxLength={3}
            value={salaryCurrency}
            onChange={(e) => setSalaryCurrency(e.target.value.toUpperCase())}
            placeholder="EUR"
          />
        </div>
        <p className="text-xs text-slate-400">
          Stored as annual gross salary. Desired salary must be at least the minimum.
        </p>
      </section>

      <section className="flex flex-col gap-3 border-t border-slate-200 pt-6">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Work model
        </h3>
        <div className="flex flex-wrap gap-4">
          {WORK_MODELS.map((model) => (
            <label key={model} className="flex items-center gap-2 text-sm capitalize text-slate-700">
              <input
                type="checkbox"
                checked={workModels.has(model)}
                onChange={() => toggleInSet(workModels, setWorkModels, model)}
                className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
              />
              {model}
            </label>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3 border-t border-slate-200 pt-6">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Employment type
        </h3>
        <div className="flex flex-wrap gap-4">
          {EMPLOYMENT_TYPES.map((type) => (
            <label key={type} className="flex items-center gap-2 text-sm capitalize text-slate-700">
              <input
                type="checkbox"
                checked={employmentTypes.has(type)}
                onChange={() => toggleInSet(employmentTypes, setEmploymentTypes, type)}
                className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
              />
              {type.replace("_", " ")}
            </label>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3 border-t border-slate-200 pt-6">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Preferred industries
        </h3>
        <Input
          label="Industries (comma separated)"
          name="preferred_industries"
          value={preferredIndustries}
          onChange={(e) => setPreferredIndustries(e.target.value)}
          placeholder="Software, Fintech"
        />
      </section>

      <section className="flex flex-col gap-3 border-t border-slate-200 pt-6">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Relocation &amp; travel
        </h3>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <label htmlFor="relocation" className="text-sm font-medium text-slate-700">
              Willing to relocate
            </label>
            <select
              id="relocation"
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900"
              value={relocation}
              onChange={(e) => setRelocation(e.target.value as typeof relocation)}
            >
              <option value="">Not specified</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
              <option value="maybe">Maybe</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="travel_willingness" className="text-sm font-medium text-slate-700">
              Willingness to travel
            </label>
            <select
              id="travel_willingness"
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900"
              value={travelWillingness}
              onChange={(e) => setTravelWillingness(e.target.value as typeof travelWillingness)}
            >
              <option value="">Not specified</option>
              <option value="none">No travel</option>
              <option value="occasional">Occasional travel</option>
              <option value="frequent">Frequent travel</option>
              <option value="flexible">Flexible</option>
            </select>
          </div>
        </div>
      </section>

      <section className="flex flex-col gap-3 border-t border-slate-200 pt-6">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Deal breakers
        </h3>
        <p className="text-xs text-slate-400">
          These don&apos;t affect anything yet &mdash; future versions will use them for matching.
        </p>
        <div className="flex flex-col gap-2">
          {DEAL_BREAKERS.map(({ type, label }) => (
            <label key={type} className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={dealBreakers[type]}
                onChange={() =>
                  setDealBreakers((prev) => ({ ...prev, [type]: !prev[type] }))
                }
                className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
              />
              {label}
            </label>
          ))}
        </div>
      </section>

      <Button type="submit" isLoading={isSaving} className="w-fit">
        {isSaving ? "Saving..." : "Save preferences"}
      </Button>
    </form>
  );
}
