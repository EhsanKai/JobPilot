"use client";

import { FormEvent, useEffect, useState } from "react";

import { getProfile, updateProfile } from "@/lib/api/profile";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { ProfileWithUser, SeniorityLevel } from "@/types/profile";

type FormState = {
  first_name: string;
  last_name: string;
  phone: string;
  location: string;
  linkedin_url: string;
  current_job_title: string;
  years_of_experience: string;
  seniority_level: string;
  industries: string;
};

const SENIORITY_LEVELS: SeniorityLevel[] = [
  "intern",
  "junior",
  "mid",
  "senior",
  "lead",
  "manager",
  "director",
];

const emptyForm: FormState = {
  first_name: "",
  last_name: "",
  phone: "",
  location: "",
  linkedin_url: "",
  current_job_title: "",
  years_of_experience: "",
  seniority_level: "",
  industries: "",
};

function toFormState(data: ProfileWithUser): FormState {
  return {
    first_name: data.first_name,
    last_name: data.last_name,
    phone: data.phone ?? "",
    location: data.location ?? "",
    linkedin_url: data.linkedin_url ?? "",
    current_job_title: data.current_job_title ?? "",
    years_of_experience: data.years_of_experience?.toString() ?? "",
    seniority_level: data.seniority_level ?? "",
    industries: data.industries.join(", "),
  };
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function ProfileForm() {
  const [form, setForm] = useState<FormState>(emptyForm);
  const [email, setEmail] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    getProfile()
      .then((data) => {
        if (!isMounted) return;
        setForm(toFormState(data));
        setEmail(data.email);
      })
      .catch((err) => {
        if (!isMounted) return;
        setError(err instanceof ApiError ? err.message : "Unable to load your profile.");
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
      const updated = await updateProfile({
        first_name: form.first_name,
        last_name: form.last_name,
        phone: form.phone || null,
        location: form.location || null,
        linkedin_url: form.linkedin_url || null,
        current_job_title: form.current_job_title || null,
        years_of_experience: form.years_of_experience ? Number(form.years_of_experience) : null,
        seniority_level: (form.seniority_level || null) as ProfileWithUser["seniority_level"],
        industries: splitList(form.industries),
      });
      setForm(toFormState(updated));
      setSuccessMessage("Profile updated successfully.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save your profile.");
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading your profile...</p>;
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6" noValidate>
      {error && <Alert variant="error">{error}</Alert>}
      {successMessage && <Alert variant="success">{successMessage}</Alert>}

      <section className="flex flex-col gap-4">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Personal</h3>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="First name"
            name="first_name"
            required
            value={form.first_name}
            onChange={(e) => updateField("first_name", e.target.value)}
          />
          <Input
            label="Last name"
            name="last_name"
            required
            value={form.last_name}
            onChange={(e) => updateField("last_name", e.target.value)}
          />
        </div>
        <Input label="Email" name="email" value={email} disabled readOnly />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="Phone"
            name="phone"
            value={form.phone}
            onChange={(e) => updateField("phone", e.target.value)}
          />
          <Input
            label="Location"
            name="location"
            value={form.location}
            onChange={(e) => updateField("location", e.target.value)}
          />
        </div>
        <Input
          label="LinkedIn URL"
          name="linkedin_url"
          type="url"
          value={form.linkedin_url}
          onChange={(e) => updateField("linkedin_url", e.target.value)}
          placeholder="https://linkedin.com/in/..."
        />
      </section>

      <section className="flex flex-col gap-4 border-t border-slate-200 pt-6">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Professional
        </h3>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="Current job title"
            name="current_job_title"
            value={form.current_job_title}
            onChange={(e) => updateField("current_job_title", e.target.value)}
          />
          <Input
            label="Years of experience"
            name="years_of_experience"
            type="number"
            min={0}
            max={80}
            value={form.years_of_experience}
            onChange={(e) => updateField("years_of_experience", e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="seniority_level" className="text-sm font-medium text-slate-700">
            Seniority level
          </label>
          <select
            id="seniority_level"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500"
            value={form.seniority_level}
            onChange={(e) => updateField("seniority_level", e.target.value)}
          >
            <option value="">Not specified</option>
            {SENIORITY_LEVELS.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </div>
        <Input
          label="Industries you've worked in (comma separated)"
          name="industries"
          value={form.industries}
          onChange={(e) => updateField("industries", e.target.value)}
          placeholder="Software, Fintech"
        />
      </section>

      <Button type="submit" isLoading={isSaving} className="w-fit">
        {isSaving ? "Saving..." : "Save changes"}
      </Button>
    </form>
  );
}
