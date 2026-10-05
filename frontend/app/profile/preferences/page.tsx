"use client";

import { AppShell } from "@/components/layout/AppShell";
import { PreferencesForm } from "@/components/profile/PreferencesForm";
import { Card } from "@/components/ui/Card";

export default function PreferencesPage() {
  return (
    <AppShell>
      <h1 className="text-2xl font-semibold text-slate-900">Job preferences</h1>
      <p className="mt-1 text-slate-500">
        These help us tailor future job matching. They are not used for matching yet.
      </p>
      <Card className="mt-6">
        <PreferencesForm />
      </Card>
    </AppShell>
  );
}
