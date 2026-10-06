"use client";

import { FormEvent, useEffect, useState } from "react";

import { addLanguage, deleteLanguage, getLanguages } from "@/lib/api/languages";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { LanguageProficiency, UserLanguage } from "@/types/languages";

const PROFICIENCIES: LanguageProficiency[] = [
  "basic",
  "conversational",
  "professional",
  "fluent",
  "native",
];

export function LanguagesManager() {
  const [languages, setLanguages] = useState<UserLanguage[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [name, setName] = useState("");
  const [proficiency, setProficiency] = useState<LanguageProficiency>("professional");

  useEffect(() => {
    let isMounted = true;
    getLanguages()
      .then((data) => {
        if (isMounted) setLanguages(data);
      })
      .catch((err) => {
        if (isMounted) {
          setError(err instanceof ApiError ? err.message : "Unable to load your languages.");
        }
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  async function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) return;

    setError(null);
    setSuccessMessage(null);
    setIsSubmitting(true);
    try {
      const created = await addLanguage({ name: name.trim(), proficiency });
      setLanguages((prev) => [...prev, created]);
      setName("");
      setSuccessMessage(`${created.name} added.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add language.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleDelete(languageId: string) {
    setError(null);
    try {
      await deleteLanguage(languageId);
      setLanguages((prev) => prev.filter((l) => l.id !== languageId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to remove language.");
    }
  }

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading your languages...</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <Alert variant="error">{error}</Alert>}
      {successMessage && <Alert variant="success">{successMessage}</Alert>}

      {languages.length === 0 ? (
        <p className="text-sm text-slate-500">No languages added yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {languages.map((language) => (
            <li
              key={language.id}
              className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-4 py-2"
            >
              <span className="font-medium text-slate-800">
                {language.name} &mdash; <span className="capitalize">{language.proficiency}</span>
              </span>
              <Button variant="danger" onClick={() => handleDelete(language.id)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-3" noValidate>
        <Input
          label="Add a language"
          name="language_name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. German"
        />
        <div className="flex flex-col gap-1">
          <label htmlFor="language_proficiency" className="text-sm font-medium text-slate-700">
            Proficiency
          </label>
          <select
            id="language_proficiency"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900"
            value={proficiency}
            onChange={(e) => setProficiency(e.target.value as LanguageProficiency)}
          >
            {PROFICIENCIES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" isLoading={isSubmitting} disabled={!name.trim()}>
          {isSubmitting ? "Adding..." : "Add language"}
        </Button>
      </form>
    </div>
  );
}
