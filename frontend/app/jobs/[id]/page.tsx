"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/layout/AppShell";
import { JobDetails } from "@/components/jobs/JobDetails";

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  return (
    <AppShell>
      <JobDetails jobId={params.id} />
    </AppShell>
  );
}
