"use client";

import { FormEvent, useEffect, useState } from "react";

import { getProfile, updateProfile } from "@/lib/api/profile";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { ProfileWithUser } from "@/types/profile";

type FormState = {
  first_name: string;
  last_name: string;
  phone: string;
  location: string;
  linkedin_url: string;
  current_position: string;
  years_of_experience: string;
};

const emptyForm: FormState = {
  first_name: "",
  last_name: "",
  phone: "",
  location: "",
  linkedin_url: "",
  current_position: "",
  years_of_experience: "",
};

function toFormState(data: ProfileWithUser): FormState {
  return {
    first_name: data.first_name,
    last_name: data.last_name,
    phone: data.profile?.phone ?? "",
    location: data.profile?.location ?? "",
    linkedin_url: data.profile?.linkedin_url ?? "",
    current_position: data.profile?.current_position ?? "",
    years_of_experience: data.profile?.years_of_experience?.toString() ?? "",
  };
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
        current_position: form.current_position || null,
        years_of_experience: form.years_of_experience ? Number(form.years_of_experience) : null,
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
    <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
      {error && <Alert variant="error">{error}</Alert>}
      {successMessage && <Alert variant="success">{successMessage}</Alert>}

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

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Input
          label="Current position"
          name="current_position"
          value={form.current_position}
          onChange={(e) => updateField("current_position", e.target.value)}
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

      <Input
        label="LinkedIn URL"
        name="linkedin_url"
        type="url"
        value={form.linkedin_url}
        onChange={(e) => updateField("linkedin_url", e.target.value)}
      />

      <Button type="submit" isLoading={isSaving} className="w-fit">
        {isSaving ? "Saving..." : "Save changes"}
      </Button>
    </form>
  );
}
