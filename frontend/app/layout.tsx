import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "JobPilot",
  description: "Your AI-powered job application assistant.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans text-slate-900 antialiased">{children}</body>
    </html>
  );
}
