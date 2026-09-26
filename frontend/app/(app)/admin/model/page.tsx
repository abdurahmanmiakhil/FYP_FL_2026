"use client";

import { AlertTriangle, CheckCircle2, Cpu, XCircle } from "lucide-react";

import { PageHeader } from "@/components/clinical";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useHealth, useModelCard } from "@/lib/api/hooks";

type Metrics = Record<string, number>;

export default function ModelCardPage() {
  const card = useModelCard();
  const health = useHealth();
  const c = card.data;
  if (card.isLoading || !c) return <Skeleton className="h-[70vh]" />;
  const tr = c.thesis_results as {
    source: string;
    fedavg_ensemble: Metrics;
    fedavg_ensemble_95ci: Record<string, [number, number]>;
    per_hospital: Record<string, Metrics>;
    single_model_mean_over_seeds: Metrics;
  };
  const td = c.training_data as Record<string, string | number>;
  const h = health.data;

  return (
    <>
      <PageHeader
        title="Model card"
        description="FedAvg seed ensemble from the BS thesis (NUML, 2026): what it is, how well it performed and where it must not be used."
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Test performance (thesis)</CardTitle>
            <CardDescription>{tr.source}</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Metric</TableHead>
                  <TableHead>FedAvg ensemble</TableHead>
                  <TableHead>95% CI</TableHead>
                  <TableHead>Single model (mean of 5 seeds)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(tr.fedavg_ensemble).map(([k, v]) => {
                  const ci = tr.fedavg_ensemble_95ci[k];
                  const single = tr.single_model_mean_over_seeds[k];
                  return (
                    <TableRow key={k}>
                      <TableCell className="font-medium">{k}</TableCell>
                      <TableCell className="font-mono tabular-nums">{v.toFixed(4)}</TableCell>
                      <TableCell className="font-mono text-xs tabular-nums">
                        {ci ? `${ci[0].toFixed(4)} - ${ci[1].toFixed(4)}` : "-"}
                      </TableCell>
                      <TableCell className="font-mono tabular-nums">
                        {single != null ? single.toFixed(3) : "-"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            <h3 className="mb-2 mt-6 text-sm font-semibold">Per test hospital (ensemble)</h3>
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Hospital</TableHead>
                  <TableHead>csPCa AUC</TableHead>
                  <TableHead>Cancer AUC</TableHead>
                  <TableHead>QWK</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(tr.per_hospital).map(([name, m]) => (
                  <TableRow key={name}>
                    <TableCell>{name}</TableCell>
                    <TableCell className="font-mono">{m["csPCa AUC"]?.toFixed(4)}</TableCell>
                    <TableCell className="font-mono">{m["cancer AUC"]?.toFixed(4)}</TableCell>
                    <TableCell className="font-mono">{m.QWK?.toFixed(4)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Deployed model</CardTitle>
            <CardDescription>Live status from the inference workers</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center gap-2">
              {c.ready ? (
                <Badge variant="success">
                  <CheckCircle2 aria-hidden /> Ready - models loaded
                </Badge>
              ) : (
                <Badge variant="destructive">
                  <XCircle aria-hidden /> Not ready
                </Badge>
              )}
            </div>
            <div>
              <p className="text-muted-foreground">Model version (sha256 of all model files + thresholds)</p>
              <p className="break-all font-mono text-xs">
                {c.model_version ?? "unknown (no worker has reported yet)"}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Preprocessing version</p>
              <p className="break-all font-mono text-xs">{c.preprocessing_version}</p>
            </div>
            {c.workers.map((w) => (
              <p key={w.worker} className="flex items-center gap-2 text-xs">
                <Cpu className="size-4 text-muted-foreground" aria-hidden /> {w.worker} on <b>{w.device}</b>
              </p>
            ))}
            {h && (
              <ul className="grid grid-cols-2 gap-1 border-t pt-3 text-xs">
                {(["database", "redis", "storage", "models_loaded"] as const).map((k) => (
                  <li key={k} className="flex items-center gap-1.5">
                    {h[k] ? (
                      <CheckCircle2 className="size-3.5 text-grade-low" aria-hidden />
                    ) : (
                      <XCircle className="size-3.5 text-destructive" aria-hidden />
                    )}
                    {k.replace("_", " ")}
                  </li>
                ))}
                <li className="col-span-2 text-muted-foreground">Queue length: {h.queue_length}</li>
              </ul>
            )}
            {Object.keys(c.thresholds).length > 0 && (
              <details className="border-t pt-3">
                <summary className="cursor-pointer font-medium">Thresholds in use</summary>
                <dl className="mt-2 grid grid-cols-2 gap-1 font-mono text-xs">
                  {Object.entries(c.thresholds).map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="text-muted-foreground">{k}</dt>
                      <dd className="text-right">{Number(v).toFixed(4)}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Training data and method</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              {Object.entries(td).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {k.replace("_", " ")}
                  </dt>
                  <dd>{typeof v === "number" ? v.toLocaleString() : v}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Intended use</CardTitle>
          </CardHeader>
          <CardContent className="text-sm leading-relaxed">{c.intended_use}</CardContent>
        </Card>

        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-warn" aria-hidden /> Limitations
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-disc space-y-1.5 pl-5 text-sm">
              {c.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
