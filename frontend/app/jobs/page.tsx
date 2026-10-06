"use client";

import { AppShell } from "@/components/layout/AppShell";
import { JobsDashboard } from "@/components/jobs/JobsDashboard";

export default function JobsPage() {
  return (
    <AppShell>
      <h1 className="text-2xl font-semibold text-slate-900">Jobs</h1>
      <p className="mt-1 text-slate-500">
        Discover real job postings from public company job boards. Every job links to its original
        posting. Matching and applying come in later versions.
      </p>
      <div className="mt-6">
        <JobsDashboard />
      </div>
    </AppShell>
  );
}
