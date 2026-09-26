"use client";

import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, Circle, Loader2, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { errorMessage, type CaseDetail } from "@/lib/api/client";
import { qk, useRetryCase } from "@/lib/api/hooks";
import { PIPELINE_STAGES, STAGE_LABEL } from "@/lib/grades";
import { cn } from "@/lib/utils";

interface Snapshot {
  status: string;
  stage: string;
  done: number;
  total: number;
  error?: string | null;
}

/** Live job progress from the server-sent event stream (falls back to polling via the case query). */
function useCaseEvents(caseId: string, active: boolean, initial: Snapshot): Snapshot {
  const qc = useQueryClient();
  const [snap, setSnap] = useState<Snapshot>(initial);
  useEffect(() => setSnap(initial), [initial.status, initial.stage, initial.done]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!active) return;
    const es = new EventSource(`/api/v1/cases/${caseId}/events`);
    es.addEventListener("progress", (ev) => {
      const s = JSON.parse((ev as MessageEvent<string>).data) as Snapshot;
      setSnap(s);
      if (s.status === "done" || s.status === "failed") {
        es.close();
        void qc.invalidateQueries({ queryKey: qk.case(caseId) });
        void qc.invalidateQueries({ queryKey: ["cases"] });
        if (s.status === "done") toast.success("AI result is ready.");
      }
    });
    return () => es.close();
  }, [caseId, active, qc]);
  return snap;
}

export function CaseProgress({ data, canRetry }: { data: CaseDetail; canRetry: boolean }) {
  const active = data.status !== "done" && data.status !== "failed";
  const snap = useCaseEvents(data.id, active, {
    status: data.status,
    stage: data.progress_stage ?? data.status,
    done: data.progress_done,
    total: data.progress_total,
    error: data.error,
  });
  const retry = useRetryCase(data.id);

  if (snap.status === "failed" || data.status === "failed") {
    return (
      <Alert variant="destructive">
        <AlertCircle aria-hidden />
        <div className="flex-1 space-y-2">
          <AlertTitle>AI analysis failed</AlertTitle>
          <AlertDescription>{snap.error ?? data.error ?? "Unknown error."}</AlertDescription>
          {canRetry && (
            <Button
              size="sm"
              variant="outline"
              loading={retry.isPending}
              onClick={() => retry.mutate(undefined, { onError: (e) => toast.error(errorMessage(e)) })}
            >
              <RotateCcw aria-hidden /> Try again
            </Button>
          )}
        </div>
      </Alert>
    );
  }

  const current = snap.stage === "retrying" ? "queued" : snap.stage;
  const idx = Math.max(0, PIPELINE_STAGES.indexOf(current));
  const encodingPct =
    snap.stage === "encoding" && snap.total ? Math.round((snap.done / snap.total) * 100) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>AI analysis in progress</CardTitle>
        <CardDescription>
          On a CPU server this takes a few minutes per slide (seconds on a GPU). You can leave this page - the
          result is saved.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="space-y-3" aria-live="polite">
          {PIPELINE_STAGES.slice(0, -1).map((stage, i) => {
            const state = i < idx ? "done" : i === idx ? "active" : "todo";
            return (
              <li key={stage} className="flex items-start gap-3">
                <span
                  className={cn(
                    "mt-0.5 flex size-5 items-center justify-center rounded-full",
                    state === "done" && "bg-primary text-primary-foreground",
                    state === "active" && "text-primary",
                    state === "todo" && "text-muted-foreground",
                  )}
                >
                  {state === "done" ? (
                    <Check className="size-3.5" aria-hidden />
                  ) : state === "active" ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <Circle className="size-3" aria-hidden />
                  )}
                </span>
                <div className="flex-1">
                  <p
                    className={cn(
                      "text-sm",
                      state === "active" ? "font-semibold" : state === "todo" && "text-muted-foreground",
                    )}
                  >
                    {STAGE_LABEL[stage]}
                    {stage === "encoding" && state === "active" && snap.total > 0 && (
                      <span className="ml-2 font-mono text-xs tabular-nums text-muted-foreground">
                        {snap.done} / {snap.total} tiles
                      </span>
                    )}
                    <span className="sr-only">
                      {" "}
                      - {state === "done" ? "completed" : state === "active" ? "in progress" : "pending"}
                    </span>
                  </p>
                  {stage === "encoding" && state === "active" && encodingPct != null && (
                    <Progress value={encodingPct} className="mt-2 h-2" aria-label="Tile encoding progress" />
                  )}
                </div>
              </li>
            );
          })}
        </ol>
        {snap.stage === "retrying" && (
          <p className="mt-3 text-sm text-warn">A temporary error occurred - retrying automatically.</p>
        )}
      </CardContent>
    </Card>
  );
}
