import type { AuditEntry } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";

const ACTION_LABEL: Record<string, string> = {
  login: "Signed in",
  login_failed: "Failed sign-in",
  logout: "Signed out",
  logout_all: "Signed out everywhere",
  token_reuse: "Refresh token reuse blocked",
  password_change: "Changed password",
  password_reset: "Password reset",
  totp_enable: "Enabled two-factor",
  totp_disable: "Disabled two-factor",
  user_create: "Created user",
  user_update: "Updated user",
  upload: "Uploaded slide",
  view: "Viewed case",
  predict: "AI prediction stored",
  predict_failed: "AI prediction failed",
  review: "Recorded review",
  export: "Exported",
  delete: "Deleted",
  retention_delete: "Deleted by retention policy",
};

export function actionLabel(a: string): string {
  return ACTION_LABEL[a] ?? a;
}

function detailText(e: AuditEntry): string {
  const d = e.details as Record<string, unknown>;
  const parts: string[] = [];
  if (d.decision) parts.push(`decision ${String(d.decision)}`);
  if (d.final_isup != null) parts.push(`final ISUP ${String(d.final_isup)}`);
  if (d.isup_grade != null) parts.push(`AI ISUP ${String(d.isup_grade)}`);
  if (d.format) parts.push(String(d.format).toUpperCase());
  if (d.reason) parts.push(String(d.reason).replace("_", " "));
  if (d.retry) parts.push("retry");
  return parts.join(", ");
}

export function AuditList({ entries }: { entries: AuditEntry[] }) {
  if (!entries.length) return <p className="text-sm text-muted-foreground">No audit entries.</p>;
  return (
    <ol className="relative space-y-3 border-l pl-4">
      {entries.map((e) => (
        <li key={e.id} className="text-sm">
          <span
            className="absolute -left-1.5 mt-1.5 size-3 rounded-full border-2 border-background bg-primary"
            aria-hidden
          />
          <p className="font-medium">
            {actionLabel(e.action)}
            {detailText(e) && <span className="font-normal text-muted-foreground"> - {detailText(e)}</span>}
          </p>
          <p className="text-xs text-muted-foreground">
            {e.user_email ?? "system"} - {formatDateTime(e.at)}
            {e.ip ? ` - ${e.ip}` : ""}
          </p>
        </li>
      ))}
    </ol>
  );
}
