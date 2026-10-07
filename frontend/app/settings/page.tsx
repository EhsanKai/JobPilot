"use client";

import { AppShell } from "@/components/layout/AppShell";
import { PrivacySettings } from "@/components/privacy/PrivacySettings";

export default function SettingsPage() {
  return (
    <AppShell>
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Settings</h1>
          <p className="mt-1 text-sm text-slate-500">Privacy &amp; data</p>
        </div>
        <PrivacySettings />
      </div>
    </AppShell>
  );
}
