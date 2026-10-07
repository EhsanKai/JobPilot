"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getPreferences } from "@/lib/api/preferences";
import { getCurrentResume } from "@/lib/api/resumes";
import { Card } from "@/components/ui/Card";
import { ProfileCompleteness } from "@/components/profile/ProfileCompleteness";
import { ApplicationsSummaryCard } from "@/components/tracking/ApplicationsSummaryCard";
import { isInProgress } from "@/lib/resumeIntelligence";
import type { JobPreferences } from "@/types/preferences";
import type { Resume } from "@/types/resume";

interface UpcomingFeature {
  title: string;
  version: string;
}

const UPCOMING_FEATURES: UpcomingFeature[] = [
  { title: "AI Matching", version: "V0.5" },
  { title: "Application Assistant", version: "V0.7" },
];

type StepState = "done" | "warning" | "pending" | "error";

interface ResumeStep {
  label: string;
  state: StepState;
}

interface ResumeWidget {
  steps: ResumeStep[];
  action: { label: string; href: string } | null;
}

/** Resume + Resume Intelligence progress for the dashboard card. */
export function describeResume(resume: Resume | null): ResumeWidget {
  if (!resume) {
    return {
      steps: [{ label: "Not uploaded", state: "pending" }],
      action: { label: "Upload Resume", href: "/profile/resume" },
    };
  }
  const uploaded: ResumeStep = { label: "Uploaded", state: "done" };
  switch (resume.status) {
    case "uploaded":
      return {
        steps: [uploaded, { label: "Not analyzed yet", state: "pending" }],
        action: { label: "Analyze Resume", href: "/profile/resume" },
      };
    case "needs_review":
      return {
        steps: [uploaded, { label: "Analyzed", state: "done" }, { label: "Review required", state: "warning" }],
        action: { label: "Review Resume", href: "/profile/resume/review" },
      };
    case "confirmed":
      return {
        steps: [uploaded, { label: "Analyzed", state: "done" }, { label: "Confirmed", state: "done" }],
        action: { label: "View Resume Profile", href: "/profile/resume/review" },
      };
    case "ocr_required":
      return {
        steps: [uploaded, { label: "Text could not be read (OCR required)", state: "error" }],
        action: { label: "Upload a text-based resume", href: "/profile/resume" },
      };
    case "failed":
      return {
        steps: [uploaded, { label: "Analysis failed", state: "error" }],
        action: { label: "Try again", href: "/profile/resume" },
      };
    default:
      return {
        steps: [uploaded, { label: "Analyzing...", state: "pending" }],
        action: isInProgress(resume.status) ? { label: "View progress", href: "/profile/resume" } : null,
      };
  }
}

const STEP_ICONS: Record<StepState, { icon: string; className: string }> = {
  done: { icon: "✓", className: "text-emerald-600" },
  warning: { icon: "⚠", className: "text-amber-600" },
  pending: { icon: "○", className: "text-slate-400" },
  error: { icon: "✕", className: "text-red-600" },
};

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
          {isLoading ? (
            <p className="mt-2 text-3xl font-semibold text-slate-900">–</p>
          ) : (
            <ResumeStatusSummary resume={resume} />
          )}
        </Card>
        <Card>
          <p className="text-sm font-medium text-slate-500">Job preferences</p>
          <p className="mt-2 text-3xl font-semibold text-slate-900">
            {isLoading ? "–" : computePreferencesStatus(preferences)}
          </p>
        </Card>
      </div>

      <Card className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-medium text-slate-900">Job Search</p>
          <p className="text-sm text-slate-500">
            Discover real postings from public company job boards.
          </p>
        </div>
        <Link
          href="/jobs"
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          Find jobs
        </Link>
      </Card>

      <ApplicationsSummaryCard />

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

function ResumeStatusSummary({ resume }: { resume: Resume | null }) {
  const { steps, action } = describeResume(resume);
  return (
    <div className="mt-2 flex flex-col gap-3">
      <ul className="flex flex-col gap-1 text-sm text-slate-700" aria-label="Resume progress">
        {steps.map((step) => (
          <li key={step.label}>
            <span className={STEP_ICONS[step.state].className} aria-hidden="true">
              {STEP_ICONS[step.state].icon}
            </span>{" "}
            {step.label}
          </li>
        ))}
      </ul>
      {action && (
        <Link href={action.href} className="text-sm font-medium text-brand-700 hover:underline">
          {action.label} →
        </Link>
      )}
    </div>
  );
}
