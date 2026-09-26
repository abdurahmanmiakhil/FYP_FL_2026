"use client";

import { AlertTriangle, CheckCircle2, Copy, Info, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { ClassProbabilityChart, GradeBadge, ProbabilityBar } from "@/components/clinical";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Prediction } from "@/lib/api/client";
import { duration, formatDateTime, shortHash } from "@/lib/format";
import { GLEASON } from "@/lib/grades";

const FLAG_LABEL: Record<string, string> = {
  cspca_youden: "csPCa - Youden threshold",
  cspca_sens90: "csPCa - 90% sensitivity threshold",
  cspca_sens95: "csPCa - 95% sensitivity threshold",
  cancer_youden: "Cancer - Youden threshold",
};

export function ResultCard({ prediction: p }: { prediction: Prediction }) {
  const t = p.thresholds;
  const reviewed = p.status === "reviewed";
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <CardTitle>AI result</CardTitle>
        {reviewed ? (
          <Badge variant="success">
            <CheckCircle2 aria-hidden /> Reviewed
          </Badge>
        ) : (
          <Badge variant="warn">
            <ShieldAlert aria-hidden /> Provisional - not yet reviewed
          </Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="rounded-md bg-warn-bg px-3 py-2 text-xs font-medium text-warn">{p.disclaimer}</p>

        {p.low_confidence_reasons.length > 0 && (
          <Alert variant="warn">
            <AlertTriangle aria-hidden />
            <div>
              <AlertTitle>Low confidence - mandatory review</AlertTitle>
              <AlertDescription>
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {p.low_confidence_reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </AlertDescription>
            </div>
          </Alert>
        )}

        <div className="flex flex-wrap items-center gap-4">
          <GradeBadge grade={p.isup_grade} size="lg" />
          <div className="text-sm">
            <p className="font-semibold">{GLEASON[p.isup_grade]}</p>
            <p className="text-muted-foreground">Gleason pattern hint for ISUP grade group {p.isup_grade}</p>
          </div>
        </div>

        <div className="space-y-4">
          <ProbabilityBar
            label="P(cancer) - ISUP ≥ 1"
            value={p.p_cancer}
            markers={t.cancer_youden != null ? [{ value: t.cancer_youden, label: "Youden" }] : []}
          />
          <ProbabilityBar
            label="P(clinically significant cancer) - ISUP ≥ 2"
            value={p.p_cspca}
            markers={[
              ...(t.cspca_youden != null ? [{ value: t.cspca_youden, label: "Youden" }] : []),
              ...(t.cspca_sens95 != null
                ? [{ value: t.cspca_sens95, label: "95% sensitivity", style: "dashed" as const }]
                : []),
            ]}
          />
        </div>

        <div>
          <h3 className="mb-2 text-sm font-medium">Probability of each ISUP grade</h3>
          <ClassProbabilityChart probs={p.p_isup} predicted={p.isup_grade} />
          <p className="mt-2 flex gap-1.5 text-xs text-muted-foreground">
            <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            The grade uses ordinal thresholds tuned on validation data (as in the thesis), so it can differ
            from the single most probable class.
          </p>
        </div>

        <div>
          <h3 className="mb-2 text-sm font-medium">Operating points (fixed on validation)</h3>
          <ul className="grid gap-1.5 text-sm sm:grid-cols-2">
            {Object.entries(p.operating_point_flags).map(([k, v]) => (
              <li key={k} className="flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5">
                <span className="text-muted-foreground">{FLAG_LABEL[k] ?? k}</span>
                <span className={v ? "font-semibold text-grade-high" : "font-semibold text-grade-low"}>
                  {v ? "Positive" : "Negative"}
                </span>
              </li>
            ))}
          </ul>
        </div>

        {Array.isArray(p.qc.warnings) && (p.qc.warnings as string[]).length > 0 && (
          <Alert>
            <Info aria-hidden />
            <div>
              <AlertTitle>Slide quality</AlertTitle>
              <AlertDescription>
                <ul className="list-disc pl-4 text-muted-foreground">
                  {(p.qc.warnings as string[]).map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </AlertDescription>
            </div>
          </Alert>
        )}

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-t pt-4 text-xs">
          <dt className="text-muted-foreground">Tiles analysed</dt>
          <dd className="text-right tabular-nums">
            {p.n_tiles} of {p.n_tiles_total} tissue tiles
          </dd>
          <dt className="text-muted-foreground">Seed agreement (SD of P(csPCa))</dt>
          <dd className="text-right tabular-nums">{p.seed_std_p_cspca.toFixed(3)}</dd>
          <dt className="text-muted-foreground">Runtime</dt>
          <dd className="text-right">
            {duration(p.runtime_seconds)} on {p.device}
          </dd>
          <dt className="text-muted-foreground">Result time</dt>
          <dd className="text-right">{formatDateTime(p.created_at)}</dd>
          <dt className="text-muted-foreground">Model version</dt>
          <dd className="text-right">
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 font-mono hover:underline"
                  onClick={() => {
                    void navigator.clipboard?.writeText(p.model_version);
                    toast.success("Model version copied");
                  }}
                  aria-label={`Model version ${p.model_version}, copy`}
                >
                  {shortHash(p.model_version)} <Copy className="size-3" aria-hidden />
                </button>
              </TooltipTrigger>
              <TooltipContent className="font-mono">{p.model_version}</TooltipContent>
            </Tooltip>
          </dd>
        </dl>
      </CardContent>
    </Card>
  );
}
