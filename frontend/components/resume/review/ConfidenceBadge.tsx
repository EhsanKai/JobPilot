import { CONFIDENCE_LABELS } from "@/lib/resumeIntelligence";
import type { Confidence } from "@/types/resumeProfile";

const DOT_CLASSES: Record<Confidence, string> = {
  high: "bg-emerald-500",
  medium: "bg-amber-400",
  low: "bg-slate-400",
};

/** Extraction certainty -- not a rating of the candidate's skill. */
export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-slate-500"
      title="How certain JobPilot is that this was extracted correctly"
    >
      <span className={`h-2 w-2 rounded-full ${DOT_CLASSES[confidence]}`} aria-hidden="true" />
      {CONFIDENCE_LABELS[confidence]}
    </span>
  );
}
