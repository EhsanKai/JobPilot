"use client";

import { FormEvent, useEffect, useState } from "react";

import { addSkill, deleteSkill, getSkills, updateSkill } from "@/lib/api/skills";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { Skill, SkillProficiency } from "@/types/skills";

const PROFICIENCIES: SkillProficiency[] = ["beginner", "intermediate", "advanced", "expert"];

export function SkillsManager() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [name, setName] = useState("");
  const [proficiency, setProficiency] = useState<string>("");
  const [years, setYears] = useState("");

  useEffect(() => {
    let isMounted = true;
    getSkills()
      .then((data) => {
        if (isMounted) setSkills(data);
      })
      .catch((err) => {
        if (isMounted) {
          setError(err instanceof ApiError ? err.message : "Unable to load your skills.");
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
      const created = await addSkill({
        name: name.trim(),
        proficiency: (proficiency || null) as SkillProficiency | null,
        years_of_experience: years ? Number(years) : null,
      });
      setSkills((prev) => [...prev.filter((s) => s.name !== created.name), created]);
      setName("");
      setProficiency("");
      setYears("");
      setSuccessMessage(`${created.name} added.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add skill.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleProficiencyChange(skill: Skill, value: string) {
    setError(null);
    try {
      const updated = await updateSkill(skill.id, {
        proficiency: (value || null) as SkillProficiency | null,
        years_of_experience: skill.years_of_experience,
      });
      setSkills((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update skill.");
    }
  }

  async function handleDelete(skillId: string) {
    setError(null);
    try {
      await deleteSkill(skillId);
      setSkills((prev) => prev.filter((s) => s.id !== skillId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to remove skill.");
    }
  }

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading your skills...</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <Alert variant="error">{error}</Alert>}
      {successMessage && <Alert variant="success">{successMessage}</Alert>}

      {skills.length === 0 ? (
        <p className="text-sm text-slate-500">No skills added yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {skills.map((skill) => (
            <li
              key={skill.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 px-4 py-2"
            >
              <span className="font-medium text-slate-800">{skill.name}</span>
              <div className="flex items-center gap-2">
                <select
                  aria-label={`Proficiency for ${skill.name}`}
                  className="rounded-lg border border-slate-300 px-2 py-1 text-sm text-slate-700"
                  value={skill.proficiency ?? ""}
                  onChange={(e) => handleProficiencyChange(skill, e.target.value)}
                >
                  <option value="">No proficiency</option>
                  {PROFICIENCIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
                <Button variant="danger" onClick={() => handleDelete(skill.id)}>
                  Remove
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-3" noValidate>
        <Input
          label="Add a skill"
          name="skill_name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. SAP ABAP"
        />
        <div className="flex flex-col gap-1">
          <label htmlFor="skill_proficiency" className="text-sm font-medium text-slate-700">
            Proficiency
          </label>
          <select
            id="skill_proficiency"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900"
            value={proficiency}
            onChange={(e) => setProficiency(e.target.value)}
          >
            <option value="">Not specified</option>
            {PROFICIENCIES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <Input
          label="Years"
          name="skill_years"
          type="number"
          min={0}
          max={80}
          value={years}
          onChange={(e) => setYears(e.target.value)}
          className="w-24"
        />
        <Button type="submit" isLoading={isSubmitting} disabled={!name.trim()}>
          {isSubmitting ? "Adding..." : "Add skill"}
        </Button>
      </form>
    </div>
  );
}
