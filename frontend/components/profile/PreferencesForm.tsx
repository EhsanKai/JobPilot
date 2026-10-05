"use client";

import { FormEvent, useEffect, useState } from "react";

import { getPreferences, updatePreferences } from "@/lib/api/preferences";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { JobPreferences } from "@/types/preferences";

type FormState = {
  desired_job_titles: string;
  preferred_locations: string;
  employment_types: string;
  industries: string;
  languages: string;
  minimum_salary: string;
  desired_salary: string;
  salary_currency: string;
  seniority: string;
  remote_preference: string;
  relocation_willingness: boolean;
  travel_willingness: string;
};

const emptyForm: FormState = {
  desired_job_titles: "",
  preferred_locations: "",
  employment_types: "",
  industries: "",
  languages: "",
  minimum_salary: "",
  desired_salary: "",
  salary_currency: "",
  seniority: "",
  remote_preference: "",
  relocation_willingness: false,
  travel_willingness: "",
};

function toFormState(data: JobPreferences): FormState {
  return {
    desired_job_titles: data.desired_job_titles.join(", "),
    preferred_locations: data.preferred_locations.join(", "),
    employment_types: data.employment_types.join(", "),
    industries: data.industries.join(", "),
    languages: data.languages.map((l) => l.language).join(", "),
    minimum_salary: data.minimum_salary?.toString() ?? "",
    desired_salary: data.desired_salary?.toString() ?? "",
    salary_currency: data.salary_currency ?? "",
    seniority: data.seniority ?? "",
    remote_preference: data.remote_preference ?? "",
    relocation_willingness: data.relocation_willingness,
    travel_willingness: data.travel_willingness ?? "",
  };
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function PreferencesForm() {
  const [form, setForm] = useState<FormState>(emptyForm);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    getPreferences()
      .then((data) => {
        if (isMounted) setForm(toFormState(data));
      })
      .catch((err) => {
        if (isMounted) {
          setError(err instanceof ApiError ? err.message : "Unable to load your preferences.");
        }
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccessMessage(null);
    setIsSaving(true);

    try {
      const updated = await updatePreferences({
        desired_job_titles: splitList(form.desired_job_titles),
        preferred_locations: splitList(form.preferred_locations),
        employment_types: splitList(form.employment_types) as JobPreferences["employment_types"],
        industries: splitList(form.industries),
        languages: splitList(form.languages).map((language) => ({ language, proficiency: null })),
        minimum_salary: form.minimum_salary ? Number(form.minimum_salary) : null,
        desired_salary: form.desired_salary ? Number(form.desired_salary) : null,
        salary_currency: form.salary_currency || null,
        seniority: (form.seniority || null) as JobPreferences["seniority"],
        remote_preference: (form.remote_preference || null) as JobPreferences["remote_preference"],
        relocation_willingness: form.relocation_willingness,
        travel_willingness: (form.travel_willingness || null) as JobPreferences["travel_willingness"],
      });
      setForm(toFormState(updated));
      setSuccessMessage("Preferences saved successfully.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save your preferences.");
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading your preferences...</p>;
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
      {error && <Alert variant="error">{error}</Alert>}
      {successMessage && <Alert variant="success">{successMessage}</Alert>}

      <Input
        label="Desired job titles (comma separated)"
        name="desired_job_titles"
        value={form.desired_job_titles}
        onChange={(e) => updateField("desired_job_titles", e.target.value)}
        placeholder="Backend Engineer, Platform Engineer"
      />
      <Input
        label="Preferred locations (comma separated)"
        name="preferred_locations"
        value={form.preferred_locations}
        onChange={(e) => updateField("preferred_locations", e.target.value)}
        placeholder="Berlin, Remote"
      />
      <Input
        label="Employment types (comma separated)"
        name="employment_types"
        value={form.employment_types}
        onChange={(e) => updateField("employment_types", e.target.value)}
        placeholder="full_time, contract"
      />
      <Input
        label="Industries (comma separated)"
        name="industries"
        value={form.industries}
        onChange={(e) => updateField("industries", e.target.value)}
        placeholder="Software, Fintech"
      />
      <Input
        label="Languages (comma separated)"
        name="languages"
        value={form.languages}
        onChange={(e) => updateField("languages", e.target.value)}
        placeholder="English, German"
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Input
          label="Minimum salary"
          name="minimum_salary"
          type="number"
          min={0}
          value={form.minimum_salary}
          onChange={(e) => updateField("minimum_salary", e.target.value)}
        />
        <Input
          label="Desired salary"
          name="desired_salary"
          type="number"
          min={0}
          value={form.desired_salary}
          onChange={(e) => updateField("desired_salary", e.target.value)}
        />
        <Input
          label="Salary currency"
          name="salary_currency"
          maxLength={3}
          value={form.salary_currency}
          onChange={(e) => updateField("salary_currency", e.target.value.toUpperCase())}
          placeholder="EUR"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="flex flex-col gap-1">
          <label htmlFor="seniority" className="text-sm font-medium text-slate-700">
            Seniority
          </label>
          <select
            id="seniority"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500"
            value={form.seniority}
            onChange={(e) => updateField("seniority", e.target.value)}
          >
            <option value="">Not specified</option>
            {["intern", "junior", "mid", "senior", "lead", "principal", "executive"].map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="remote_preference" className="text-sm font-medium text-slate-700">
            Remote preference
          </label>
          <select
            id="remote_preference"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500"
            value={form.remote_preference}
            onChange={(e) => updateField("remote_preference", e.target.value)}
          >
            <option value="">Not specified</option>
            {["onsite", "remote", "hybrid", "flexible"].map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="travel_willingness" className="text-sm font-medium text-slate-700">
            Travel willingness
          </label>
          <select
            id="travel_willingness"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500"
            value={form.travel_willingness}
            onChange={(e) => updateField("travel_willingness", e.target.value)}
          >
            <option value="">Not specified</option>
            {["none", "occasional", "frequent"].map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          checked={form.relocation_willingness}
          onChange={(e) => updateField("relocation_willingness", e.target.checked)}
          className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
        />
        Willing to relocate
      </label>

      <Button type="submit" isLoading={isSaving} className="w-fit">
        {isSaving ? "Saving..." : "Save preferences"}
      </Button>
    </form>
  );
}
