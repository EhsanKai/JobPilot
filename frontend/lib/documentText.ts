import { describeDates } from "@/lib/documents";
import type { CoverLetterContent, TailoredCvContent } from "@/types/documents";

/** A letter as plain text: what the user pastes into an application form or e-mail. */
export function letterToText(content: CoverLetterContent): string {
  const sender = [
    content.sender.full_name,
    content.sender.email,
    content.sender.phone,
    content.sender.location,
  ].filter(Boolean);
  const parts: string[] = [];
  if (sender.length > 0) parts.push(sender.join("\n"));
  if (content.recipient.company_name) parts.push(content.recipient.company_name);
  parts.push(content.subject);
  parts.push(content.salutation);
  for (const paragraph of content.paragraphs) parts.push(paragraph.text);
  if (content.salary_statement) parts.push(content.salary_statement);
  parts.push([content.closing_formula, content.signature].filter(Boolean).join("\n\n"));
  return parts.join("\n\n");
}

/** A CV as plain text. */
export function cvToText(content: TailoredCvContent): string {
  const { header } = content;
  const lines: string[] = [];
  if (header.full_name) lines.push(header.full_name);
  const contact = [header.email, header.phone, header.location, ...header.links].filter(Boolean);
  if (contact.length > 0) lines.push(contact.join(" · "));
  lines.push("", "SUMMARY", content.summary.text);
  if (content.experience.length > 0) {
    lines.push("", "EXPERIENCE");
    for (const entry of content.experience) {
      lines.push(
        "",
        [entry.job_title, entry.company].filter(Boolean).join(" — "),
        [entry.location, describeDates(entry)].filter(Boolean).join(" · ")
      );
      for (const bullet of entry.bullets) lines.push(`- ${bullet.text}`);
    }
  }
  if (content.skills.length > 0) {
    lines.push("", "SKILLS", content.skills.map((skill) => skill.name).join(", "));
  }
  if (content.education.length > 0) {
    lines.push("", "EDUCATION");
    for (const entry of content.education) {
      const dates = describeDates(entry);
      lines.push(
        `${[entry.degree, entry.field_of_study, entry.institution].filter(Boolean).join(", ")}${
          dates ? ` (${dates})` : ""
        }`
      );
    }
  }
  if (content.languages.length > 0) {
    lines.push("", "LANGUAGES");
    for (const entry of content.languages) {
      lines.push(`${entry.language}${entry.proficiency ? ` — ${entry.proficiency}` : ""}`);
    }
  }
  if (content.certifications.length > 0) {
    lines.push("", "CERTIFICATIONS");
    for (const entry of content.certifications) {
      lines.push([entry.name, entry.issuer, entry.date].filter(Boolean).join(", "));
    }
  }
  if (content.projects.length > 0) {
    lines.push("", "PROJECTS");
    for (const entry of content.projects) {
      lines.push(
        `${entry.name}${entry.role ? ` (${entry.role})` : ""}${
          entry.description ? ` — ${entry.description}` : ""
        }`
      );
    }
  }
  if (content.additional_information.length > 0) {
    lines.push("", "ADDITIONAL INFORMATION");
    for (const entry of content.additional_information) lines.push(entry.value);
  }
  return lines.join("\n");
}

export function documentToText(content: CoverLetterContent | TailoredCvContent): string {
  return content.kind === "cover_letter" ? letterToText(content) : cvToText(content);
}

/** Offer text as a .txt file download (the user's own copy, outside JobPilot). */
export function downloadText(fileName: string, text: string): void {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
