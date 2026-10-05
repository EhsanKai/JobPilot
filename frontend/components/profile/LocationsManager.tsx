"use client";

import { FormEvent, useState } from "react";

import { addLocation, deleteLocation } from "@/lib/api/preferences";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { PreferredLocation } from "@/types/preferences";

interface LocationsManagerProps {
  locations: PreferredLocation[];
  onChange: (locations: PreferredLocation[]) => void;
}

export function LocationsManager({ locations, onChange }: LocationsManagerProps) {
  const [location, setLocation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!location.trim()) return;

    setError(null);
    setIsSubmitting(true);
    try {
      const created = await addLocation({ location: location.trim() });
      onChange([...locations, created]);
      setLocation("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add location.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleDelete(locationId: string) {
    setError(null);
    try {
      await deleteLocation(locationId);
      onChange(locations.filter((loc) => loc.id !== locationId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to remove location.");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <Alert variant="error">{error}</Alert>}

      {locations.length === 0 ? (
        <p className="text-sm text-slate-500">No preferred locations added yet.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {locations.map((loc) => (
            <li
              key={loc.id}
              className="flex items-center gap-2 rounded-full border border-slate-200 px-3 py-1 text-sm"
            >
              <span className="text-slate-800">{loc.location}</span>
              <button
                type="button"
                aria-label={`Remove ${loc.location}`}
                onClick={() => handleDelete(loc.id)}
                className="text-slate-400 hover:text-red-600"
              >
                &times;
              </button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-3" noValidate>
        <Input
          label="Add a preferred location"
          name="location"
          value={location}
          onChange={(e) => setLocation(e.target.value)}
          placeholder="e.g. Cologne or Remote"
        />
        <Button type="submit" isLoading={isSubmitting} disabled={!location.trim()}>
          {isSubmitting ? "Adding..." : "Add location"}
        </Button>
      </form>
    </div>
  );
}
