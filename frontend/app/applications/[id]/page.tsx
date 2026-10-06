"use client";

import { useParams } from "next/navigation";

import { ApplicationWorkspace } from "@/components/applications/ApplicationWorkspace";
import { AppShell } from "@/components/layout/AppShell";

export default function ApplicationWorkspacePage() {
  const params = useParams<{ id: string }>();
  return (
    <AppShell>
      <ApplicationWorkspace applicationId={params.id} />
    </AppShell>
  );
}
