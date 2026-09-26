/** Small clinical display components shared by the dashboard, case list and case page. */
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { CaseStatus, Decision } from "@/lib/api/client";
import { BAND_CLASSES, BAND_LABEL, DECISION_LABEL, STAGE_LABEL, gradeBand } from "@/lib/grades";
import { cn } from "@/lib/utils";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** ISUP grade with its colour band AND a text label (never colour alone). */
export function GradeBadge({
  grade,
  size = "sm",
  showBand = true,
}: {
  grade: number | null | undefined;
  size?: "sm" | "lg";
  showBand?: boolean;
}) {
  if (grade == null) return <span className="text-sm text-muted-foreground">-</span>;
  const band = gradeBand(grade);
  const c = BAND_CLASSES[band];
  if (size === "lg") {
    return (
      <div className={cn("inline-flex flex-col rounded-lg border px-4 py-2", c.bg, c.border)}>
        <span className={cn("text-4xl font-bold leading-none", c.text)}>ISUP {grade}</span>
        {showBand && (
          <span className={cn("mt-1 text-xs font-semibold uppercase tracking-wide", c.text)}>
            {BAND_LABEL[band]}
          </span>
        )}
      </div>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-semibold",
        c.bg,
        c.border,
        c.text,
      )}
      title={BAND_LABEL[band]}
    >
      ISUP {grade}
      {showBand && <span className="sr-only">({BAND_LABEL[band]})</span>}
    </span>
  );
}

export function StatusChip({
  status,
  stage,
  done,
  total,
}: {
  status: CaseStatus;
  stage?: string | null;
  done?: number;
  total?: number;
}) {
  if (status === "done") {
    return (
      <Badge variant="success">
        <CheckCircle2 aria-hidden /> Result ready
      </Badge>
    );
  }
  if (status === "failed") {
    return (
      <Badge variant="destructive">
        <XCircle aria-hidden /> Failed
      </Badge>
    );
  }
  const label = STAGE_LABEL[stage ?? status] ?? stage ?? status;
  const progress = stage === "encoding" && total ? ` ${done ?? 0}/${total}` : "";
  return (
    <Badge variant="secondary" aria-live="polite">
      {status === "processing" ? (
        <Loader2 className="animate-spin" aria-hidden />
      ) : (
        <CircleDashed aria-hidden />
      )}
      {label}
      {progress}
    </Badge>
  );
}

export function DecisionBadge({ decision }: { decision: Decision | null | undefined }) {
  if (!decision) {
    return (
      <Badge variant="warn">
        <AlertTriangle aria-hidden /> Needs review
      </Badge>
    );
  }
  const variant = decision === "confirmed" ? "success" : decision === "amended" ? "warn" : "destructive";
  return <Badge variant={variant}>{DECISION_LABEL[decision]}</Badge>;
}

export interface ThresholdMarker {
  value: number;
  label: string;
  style?: "solid" | "dashed";
}

/** Horizontal probability bar with labelled threshold markers. */
export function ProbabilityBar({
  label,
  value,
  markers = [],
  tone = "primary",
}: {
  label: string;
  value: number;
  markers?: ThresholdMarker[];
  tone?: "primary" | "grade";
}) {
  const pct = Math.max(0, Math.min(100, value * 100));
  const fill =
    tone === "grade" ? BAND_CLASSES[value >= 0.5 ? "high" : value >= 0.2 ? "mid" : "low"].fill : "bg-primary";
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium">{label}</span>
        <span className="font-mono font-semibold tabular-nums">{pct.toFixed(1)}%</span>
      </div>
      <div
        className="relative h-3 rounded-full bg-secondary"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Number(pct.toFixed(1))}
      >
        <div className={cn("h-full rounded-full", fill)} style={{ width: `${pct}%` }} />
        {markers.map((m) => (
          <Tooltip key={m.label}>
            <TooltipTrigger asChild>
              <span
                className={cn(
                  "absolute -top-1 h-5 w-0 border-l-2 border-foreground/80",
                  m.style === "dashed" && "border-dashed",
                )}
                style={{ left: `${m.value * 100}%` }}
                tabIndex={0}
                role="img"
                aria-label={`${m.label} threshold ${(m.value * 100).toFixed(1)}%`}
              />
            </TooltipTrigger>
            <TooltipContent>
              {m.label}: {(m.value * 100).toFixed(1)}%
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
      {markers.length > 0 && (
        <p className="mt-1.5 flex flex-wrap gap-x-4 text-xs text-muted-foreground">
          {markers.map((m) => (
            <span key={m.label} className="inline-flex items-center gap-1.5">
              <span
                className={cn(
                  "inline-block h-3 w-0 border-l-2 border-foreground/80",
                  m.style === "dashed" && "border-dashed",
                )}
              />
              {m.label} {(m.value * 100).toFixed(1)}%
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

/** 6-class ISUP probability chart (bars + visible numbers, so no information is colour-only). */
export function ClassProbabilityChart({ probs, predicted }: { probs: number[]; predicted: number }) {
  return (
    <figure>
      <figcaption className="sr-only">Probability of each ISUP grade</figcaption>
      <div className="grid grid-cols-6 gap-2" role="list">
        {probs.map((p, g) => {
          const c = BAND_CLASSES[gradeBand(g)];
          return (
            <div
              key={g}
              role="listitem"
              className="flex flex-col items-center gap-1"
              aria-label={`ISUP ${g}: ${(p * 100).toFixed(1)}%`}
            >
              <span className="font-mono text-xs tabular-nums">{(p * 100).toFixed(0)}%</span>
              <div className="flex h-24 w-full items-end rounded bg-secondary">
                <div
                  className={cn("w-full rounded", c.fill)}
                  style={{ height: `${Math.max(2, p * 100)}%` }}
                />
              </div>
              <span
                className={cn(
                  "text-xs font-semibold",
                  g === predicted && "rounded bg-foreground px-1 text-background",
                )}
              >
                {g}
              </span>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-center text-xs text-muted-foreground">ISUP grade (highlighted: AI grade)</p>
    </figure>
  );
}

export function KpiCard({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon: typeof Loader2;
}) {
  return (
    <div className="rounded-lg border bg-card p-5 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <span className="rounded-md bg-primary/10 p-2 text-primary">
          <Icon className="size-4" aria-hidden />
        </span>
      </div>
      <p className="mt-2 text-3xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function GradeDistribution({ counts }: { counts: Record<string, number> }) {
  const values = [0, 1, 2, 3, 4, 5].map((g) => counts[String(g)] ?? 0);
  const max = Math.max(1, ...values);
  const total = values.reduce((a, b) => a + b, 0);
  return (
    <figure>
      <div className="flex h-44 items-end gap-3" role="list" aria-label="AI grade distribution">
        {values.map((v, g) => (
          <div
            key={g}
            role="listitem"
            className="flex flex-1 flex-col items-center gap-1"
            aria-label={`ISUP ${g}: ${v} cases`}
          >
            <span className="text-xs tabular-nums text-muted-foreground">{v}</span>
            <div className="flex h-32 w-full items-end">
              <div
                className={cn("w-full rounded-t", BAND_CLASSES[gradeBand(g)].fill)}
                style={{ height: `${(v / max) * 100}%`, minHeight: v ? 4 : 0 }}
              />
            </div>
            <span className="text-xs font-medium">ISUP {g}</span>
          </div>
        ))}
      </div>
      <figcaption className="mt-3 text-xs text-muted-foreground">
        {total} AI result{total === 1 ? "" : "s"} - green: ISUP 0-1, amber: 2-3, red: 4-5
      </figcaption>
    </figure>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Loader2;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-10 text-center">
      <Icon className="size-8 text-muted-foreground" aria-hidden />
      <p className="font-medium">{title}</p>
      {children && <div className="text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}
