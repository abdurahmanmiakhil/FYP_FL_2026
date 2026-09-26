"use client";

import {
  AlertTriangle,
  CalendarClock,
  ClipboardCheck,
  FileSearch,
  Handshake,
  Inbox,
  Upload,
} from "lucide-react";
import Link from "next/link";

import {
  DecisionBadge,
  EmptyState,
  GradeBadge,
  GradeDistribution,
  KpiCard,
  PageHeader,
  StatusChip,
} from "@/components/clinical";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCases, useHealth, useStats } from "@/lib/api/hooks";
import { useAuth } from "@/lib/auth";
import { duration, pct, timeAgo } from "@/lib/format";

export default function DashboardPage() {
  const { user, hasRole } = useAuth();
  const stats = useStats();
  const queue = useCases({ needs_review: true, sort: "-p_cspca", page_size: 8 });
  const recent = useCases({ sort: "-created_at", page_size: 8 });
  const health = useHealth();
  const s = stats.data;
  const canUpload = hasRole("pathologist", "urologist", "admin");

  return (
    <>
      <PageHeader
        title={`Welcome, ${user?.full_name ?? ""}`}
        description={hasRole("admin") ? "All hospitals" : `Cases of ${user?.hospital ?? "your hospital"}`}
        actions={
          canUpload && (
            <Button asChild>
              <Link href="/cases/new">
                <Upload aria-hidden /> Upload slide
              </Link>
            </Button>
          )
        }
      />

      {health.data && !health.data.models_loaded && (
        <div className="mb-6 flex items-center gap-2 rounded-lg border border-warn/40 bg-warn-bg px-4 py-3 text-sm text-warn">
          <AlertTriangle className="size-4" aria-hidden />
          The AI worker is not ready yet (models loading or offline). Uploads are queued and processed when it
          starts.
        </div>
      )}

      <section aria-label="Key figures" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.isLoading || !s ? (
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-32" />)
        ) : (
          <>
            <KpiCard
              icon={CalendarClock}
              label="Cases today"
              value={s.cases_today}
              hint={`${s.total_cases} in total`}
            />
            <KpiCard
              icon={ClipboardCheck}
              label="Awaiting review"
              value={s.awaiting_review}
              hint={
                s.low_confidence_awaiting
                  ? `${s.low_confidence_awaiting} flagged low confidence`
                  : "AI results not yet reviewed"
              }
            />
            <KpiCard
              icon={FileSearch}
              label="Median turnaround"
              value={duration(s.median_turnaround_s)}
              hint={
                s.mean_runtime_s != null
                  ? `model runtime ${duration(s.mean_runtime_s)} on average`
                  : "upload to AI result"
              }
            />
            <KpiCard
              icon={Handshake}
              label="Model / reviewer agreement"
              value={s.agreement_pct == null ? "-" : `${s.agreement_pct}%`}
              hint={`${s.reviewed} reviewed case${s.reviewed === 1 ? "" : "s"} (final ISUP = AI ISUP)`}
            />
          </>
        )}
      </section>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader className="flex-row items-start justify-between gap-2">
            <div>
              <CardTitle>Needs review</CardTitle>
              <CardDescription>Unreviewed AI results, highest P(csPCa) first</CardDescription>
            </div>
            <Button variant="outline" size="sm" asChild>
              <Link href="/cases?needs_review=true">View all</Link>
            </Button>
          </CardHeader>
          <CardContent>
            {queue.isLoading ? (
              <Skeleton className="h-48" />
            ) : !queue.data?.items.length ? (
              <EmptyState icon={Inbox} title="Nothing waiting for review">
                New AI results appear here until a clinician reviews them.
              </EmptyState>
            ) : (
              <ul className="divide-y">
                {queue.data.items.map((c) => (
                  <li key={c.id}>
                    <Link
                      href={`/cases/${c.id}`}
                      className="flex items-center gap-4 rounded-md px-2 py-3 transition-colors hover:bg-accent focus-visible:bg-accent"
                    >
                      <GradeBadge grade={c.isup_grade} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">{c.patient_code}</p>
                        <p className="text-xs text-muted-foreground">
                          P(csPCa) {pct(c.p_cspca)} - {timeAgo(c.created_at)}
                        </p>
                      </div>
                      {c.low_confidence && (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-warn">
                          <AlertTriangle className="size-3.5" aria-hidden /> Low confidence
                        </span>
                      )}
                      <DecisionBadge decision={c.review_decision} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AI grade distribution</CardTitle>
            <CardDescription>Latest result per case</CardDescription>
          </CardHeader>
          <CardContent>
            {s ? <GradeDistribution counts={s.by_grade} /> : <Skeleton className="h-44" />}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Recent activity</CardTitle>
          <CardDescription>Latest uploads and their status</CardDescription>
        </CardHeader>
        <CardContent>
          {recent.isLoading ? (
            <Skeleton className="h-40" />
          ) : !recent.data?.items.length ? (
            <EmptyState icon={Upload} title="No cases yet">
              {canUpload ? (
                <Link className="text-primary underline" href="/cases/new">
                  Upload the first slide
                </Link>
              ) : (
                "Uploaded slides will appear here."
              )}
            </EmptyState>
          ) : (
            <ul className="divide-y">
              {recent.data.items.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center gap-3 py-2.5">
                  <Link href={`/cases/${c.id}`} className="font-medium text-primary hover:underline">
                    {c.patient_code}
                  </Link>
                  <span className="text-xs text-muted-foreground">
                    uploaded {timeAgo(c.created_at)}
                    {c.uploaded_by_name ? ` by ${c.uploaded_by_name}` : ""}
                  </span>
                  <span className="flex-1" />
                  <GradeBadge grade={c.isup_grade} />
                  <StatusChip
                    status={c.status}
                    stage={c.progress_stage}
                    done={c.progress_done}
                    total={c.progress_total}
                  />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}
