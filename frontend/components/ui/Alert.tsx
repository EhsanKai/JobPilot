type AlertVariant = "error" | "success" | "info";

interface AlertProps {
  variant: AlertVariant;
  children: React.ReactNode;
}

const variantClasses: Record<AlertVariant, string> = {
  error: "bg-red-50 text-red-800 border-red-200",
  success: "bg-emerald-50 text-emerald-800 border-emerald-200",
  info: "bg-slate-50 text-slate-700 border-slate-200",
};

export function Alert({ variant, children }: AlertProps) {
  return (
    <div role={variant === "error" ? "alert" : "status"} className={`rounded-lg border px-4 py-3 text-sm ${variantClasses[variant]}`}>
      {children}
    </div>
  );
}
