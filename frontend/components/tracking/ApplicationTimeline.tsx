import { describeTimelineEntry } from "@/lib/tracking";
import type { TimelineEntry } from "@/types/application";

const TONE_CLASSES: Record<string, string> = {
  active: "border-sky-300 bg-sky-50 text-sky-800",
  positive: "border-emerald-300 bg-emerald-50 text-emerald-800",
  rejected: "border-red-300 bg-red-50 text-red-800",
  withdrawn: "border-amber-300 bg-amber-50 text-amber-800",
  no_response: "border-slate-300 bg-slate-50 text-slate-600",
  warning: "border-amber-300 bg-amber-50 text-amber-800",
  neutral: "border-slate-300 bg-white text-slate-600",
};

/**
 * The application's history, newest first: status changes, corrections, the user's own notes
 * (shown exactly as written) and V0.11 submission attempts. Read-only: an entry is never edited.
 */
export function ApplicationTimeline({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm text-slate-500">Nothing has happened yet.</p>;
  }
  return (
    <ol className="flex flex-col gap-4" aria-label="Application timeline">
      {entries.map((entry) => {
        const line = describeTimelineEntry(entry);
        return (
          <li key={`${entry.kind}-${entry.id}`} className="flex gap-3" data-kind={entry.kind}>
            <span
              className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-sm ${TONE_CLASSES[line.tone]}`}
              aria-hidden="true"
            >
              {line.icon}
            </span>
            <div className="min-w-0">
              <p className="text-xs text-slate-500">{line.when}</p>
              <p className="font-medium text-slate-900">{line.title}</p>
              {line.detail && <p className="text-sm text-slate-600">{line.detail}</p>}
              {line.note && (
                <blockquote className="mt-1 whitespace-pre-wrap break-words border-l-2 border-slate-300 pl-3 text-sm text-slate-700">
                  {line.note}
                </blockquote>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
