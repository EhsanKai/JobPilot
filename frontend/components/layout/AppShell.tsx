"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { useRequireAuth } from "@/lib/useAuth";
import type { User } from "@/types/user";

const NAV_LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/jobs", label: "Jobs" },
  { href: "/profile", label: "Profile" },
  { href: "/profile/preferences", label: "Preferences" },
  { href: "/profile/resume", label: "Resume" },
];

interface AppShellProps {
  children: React.ReactNode | ((user: User) => React.ReactNode);
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const { user, isLoading, logout } = useRequireAuth();

  if (isLoading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-slate-500">Loading...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link href="/dashboard" className="text-lg font-semibold text-brand-700">
            JobPilot
          </Link>
          <nav className="flex items-center gap-4">
            {NAV_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className={`text-sm font-medium ${
                  pathname === link.href ||
                  (link.href === "/jobs" && pathname.startsWith("/jobs/"))
                    ? "text-brand-700"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                {link.label}
              </Link>
            ))}
            <Button variant="secondary" onClick={logout}>
              Log out
            </Button>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">
        {typeof children === "function" ? children(user) : children}
      </main>
    </div>
  );
}
