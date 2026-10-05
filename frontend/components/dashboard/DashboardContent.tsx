"use client";

import { useEffect, useState } from "react";

import { getPreferences } from "@/lib/api/preferences";
import { getCurrentResume } from "@/lib/api/resumes";
import { Card } from "@/components/ui/Card";
import { ProfileCompleteness } from "@/components/profile/ProfileCompleteness";
import type { JobPreferences } from "@/types/preferences";
import type { Resume } from "@/types/resume";

interface UpcomingFeature {
  title: string;
  version: string;
}

const UPCOMING_FEATURES: UpcomingFeature[] = [
  { title: "Job Search", version: "V0.3" },
  { title: "AI Matching", version: "V0.5" },
  { title: "Application Assistant", version: "V0.7" },
  { title: "Application Tracking", version: "V0.12" },
];

function computePreferencesStatus(preferences: JobPreferences | null): string {
  if (!preferences) return "Not started";
  const hasAnything =
    preferences.desired_job_titles.length > 0 ||
    preferences.preferred_locations.length > 0 ||
    preferences.employment_types.length > 0;
  return hasAnything ? "Complete" : "Not started";
}

export function DashboardContent({ firstName }: { firstName: string }) {
  const [resume, setResume] = useState<Resume | null>(null);
  const [preferences, setPreferences] = useState<JobPreferences | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    Promise.all([getCurrentResume(), getPreferences()])
      .then(([resumeData, preferencesData]) => {
        if (!isMounted) return;
        setResume(resumeData);
        setPreferences(preferencesData);
      })
      .catch(() => {
        // Non-critical widgets; leave them in their empty state on failure.
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Welcome back, {firstName}</h1>
        <p className="mt-1 text-slate-500">Here&apos;s where your job search stands today.</p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <ProfileCompleteness />
        </Card>
        <Card>
          <p className="text-sm font-medium text-slate-500">Resume status</p>
          <p className="mt-2 text-3xl font-semibold text-slate-900 capitalize">
            {isLoading ? "–" : resume?.status ?? "Not uploaded"}
          </p>
        </Card>
        <Card>
          <p className="text-sm font-medium text-slate-500">Job preferences</p>
          <p className="mt-2 text-3xl font-semibold text-slate-900">
            {isLoading ? "–" : computePreferencesStatus(preferences)}
          </p>
        </Card>
      </div>

      <div>
        <h2 className="text-lg font-semibold text-slate-900">Upcoming features</h2>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {UPCOMING_FEATURES.map((feature) => (
            <Card key={feature.title} className="flex items-center justify-between">
              <span className="font-medium text-slate-700">{feature.title}</span>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-500">
                Coming in {feature.version}
              </span>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
