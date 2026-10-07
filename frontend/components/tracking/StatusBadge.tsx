import { STATUS_PRESENTATION, statusLabel } from "@/lib/tracking";
import type { ApplicationStatus } from "@/types/application";

/**
 * An application's status: the text label is the meaning, the symbol and the colour only
 * reinforce it -- so the state is never conveyed by colour alone. The symbol sits beside the
 * label, not inside it: an `aria-label` (when given) names the label text alone.
 */
export function StatusBadge({
  status,
  className = "",
  ...rest
}: {
  status: ApplicationStatus;
  className?: string;
  "aria-label"?: string;
}) {
  const presentation = STATUS_PRESENTATION[status];
  return (
    <span
      data-status={status}
      data-tone={presentation.tone}
      title={presentation.description}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium ${presentation.className} ${className}`}
    >
      <span aria-hidden="true">{presentation.icon}</span>
      <span {...rest}>{statusLabel(status)}</span>
    </span>
  );
}
