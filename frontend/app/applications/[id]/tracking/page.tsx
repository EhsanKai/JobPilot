"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/layout/AppShell";
import { ApplicationTracking } from "@/components/tracking/ApplicationTracking";

export default function ApplicationTrackingPage() {
  const params = useParams<{ id: string }>();
  return (
    <AppShell>
      <ApplicationTracking applicationId={params.id} />
    </AppShell>
  );
}
