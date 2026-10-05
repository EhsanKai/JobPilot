"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { DesiredRolesManager } from "@/components/profile/DesiredRolesManager";
import { LocationsManager } from "@/components/profile/LocationsManager";
import { PreferencesForm } from "@/components/profile/PreferencesForm";
import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { getPreferences } from "@/lib/api/preferences";
import { ApiError } from "@/lib/http";
import type { JobPreferences } from "@/types/preferences";

export default function PreferencesPage() {
  const [preferences, setPreferences] = useState<JobPreferences | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    getPreferences()
      .then((data) => {
        if (isMounted) setPreferences(data);
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

  return (
    <AppShell>
      <h1 className="text-2xl font-semibold text-slate-900">Job preferences</h1>
      <p className="mt-1 text-slate-500">
        These help us tailor future job matching. They are not used for matching yet.
      </p>

      {isLoading && <p className="mt-6 text-sm text-slate-500">Loading your preferences...</p>}
      {error && (
        <Alert variant="error">
          <span className="mt-6 block">{error}</span>
        </Alert>
      )}

      {preferences && (
        <>
          <Card className="mt-6">
            <h2 className="mb-4 text-lg font-semibold text-slate-900">Desired roles</h2>
            <DesiredRolesManager
              jobTitles={preferences.desired_job_titles}
              onChange={(desired_job_titles) =>
                setPreferences({ ...preferences, desired_job_titles })
              }
            />
          </Card>

          <Card className="mt-6">
            <h2 className="mb-4 text-lg font-semibold text-slate-900">Preferred locations</h2>
            <LocationsManager
              locations={preferences.preferred_locations}
              onChange={(preferred_locations) =>
                setPreferences({ ...preferences, preferred_locations })
              }
            />
          </Card>

          <Card className="mt-6">
            <PreferencesForm preferences={preferences} onSaved={setPreferences} />
          </Card>
        </>
      )}
    </AppShell>
  );
}
