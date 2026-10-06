"use client";

import { AppShell } from "@/components/layout/AppShell";
import { DashboardContent } from "@/components/dashboard/DashboardContent";

export default function DashboardPage() {
  return <AppShell>{(user) => <DashboardContent firstName={user.first_name} />}</AppShell>;
}
