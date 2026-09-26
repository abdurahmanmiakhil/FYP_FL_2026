"use client";

import { ArrowLeft, FileDown, History, ScrollText, Stethoscope, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useRef, useState } from "react";
import { toast } from "sonner";

import { AuditList } from "@/components/audit-list";
import { CaseProgress } from "@/components/case-progress";
import { StatusChip } from "@/components/clinical";
import { ResultCard } from "@/components/result-card";
import { ReviewForm, ReviewHistory } from "@/components/review-panel";
import { SlideViewer, type SlideViewerHandle } from "@/components/slide-viewer";
import { TopTiles } from "@/components/top-tiles";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, errorMessage } from "@/lib/api/client";
import { useCase, useCaseAudit, useDeleteCase } from "@/lib/api/hooks";
import { useAuth } from "@/lib/auth";
import { bytes, formatDateTime } from "@/lib/format";

const TILE = 224;

export default function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { hasRole } = useAuth();
  const { data, isLoading, error } = useCase(id);
  const isAdmin = hasRole("admin");
  const canReview = hasRole("pathologist", "urologist");
  const audit = useCaseAudit(id, isAdmin);
  const del = useDeleteCase();
  const viewer = useRef<SlideViewerHandle>(null);
  const [tab, setTab] = useState("review");

  if (isLoading) {
    return (
      <div className="grid gap-4 lg:grid-cols-12" aria-busy="true">
        <Skeleton className="h-[70vh] lg:col-span-7" />
        <div className="space-y-4 lg:col-span-5">
          <Skeleton className="h-72" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }
  if (error || !data) {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <Alert variant="destructive">
        <AlertDescription>
          {notFound ? "This case does not exist or belongs to another hospital." : errorMessage(error)}
        </AlertDescription>
      </Alert>
    );
  }

  const p = data.prediction;
  const mpp = (p?.qc.mpp as number | null | undefined) ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/cases">
            <ArrowLeft aria-hidden /> Cases
          </Link>
        </Button>
        <h1 className="text-xl font-semibold tracking-tight">{data.patient_code}</h1>
        <StatusChip
          status={data.status}
          stage={data.progress_stage}
          done={data.progress_done}
          total={data.progress_total}
        />
        <span className="text-sm text-muted-foreground">
          {data.hospital} - uploaded {formatDateTime(data.created_at)}
          {data.uploaded_by_name ? ` by ${data.uploaded_by_name}` : ""} - {data.slide_format.toUpperCase()}{" "}
          {bytes(data.slide_bytes)}
        </span>
        <span className="flex-1" />
        {p && (
          <Button variant="outline" asChild>
            <a href={`/api/v1/cases/${id}/report.pdf`} download>
              <FileDown aria-hidden /> Download PDF report
            </a>
          </Button>
        )}
        {isAdmin && (
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="outline" className="text-destructive">
                <Trash2 aria-hidden /> Delete
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Delete this case?</DialogTitle>
                <DialogDescription>
                  The case is hidden immediately and permanently deleted after 30 days by the retention job.
                  The deletion is recorded in the audit trail.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="outline">Cancel</Button>
                </DialogClose>
                <Button
                  variant="destructive"
                  loading={del.isPending}
                  onClick={() =>
                    del.mutate(id, {
                      onSuccess: () => {
                        toast.success("Case deleted.");
                        router.push("/cases");
                      },
                      onError: (e) => toast.error(errorMessage(e)),
                    })
                  }
                >
                  Delete case
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <section className="lg:col-span-7" aria-label="Slide viewer">
          <div className="lg:sticky lg:top-20 lg:h-[calc(100vh-7rem)]">
            <SlideViewer
              ref={viewer}
              dziUrl={data.dzi_url}
              heatmapUrl={p?.heatmap_url ?? null}
              mpp={mpp}
              label={`slide of patient ${data.patient_code}`}
            />
          </div>
        </section>

        <div className="space-y-4 lg:col-span-5">
          {data.status !== "done" || !p ? (
            <CaseProgress data={data} canRetry={hasRole("pathologist", "urologist", "admin")} />
          ) : (
            <>
              <ResultCard prediction={p} />
              <Card>
                <CardHeader>
                  <CardTitle>Top attention tiles</CardTitle>
                  <CardDescription>
                    The 8 regions that influenced the result most. Click one to show it in the viewer.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <TopTiles tiles={p.top_tiles} onSelect={(x, y) => viewer.current?.focus(x, y, TILE)} />
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-5">
                  <Tabs value={tab} onValueChange={setTab}>
                    <TabsList className="w-full">
                      <TabsTrigger value="review" className="flex-1">
                        <Stethoscope aria-hidden /> Review
                      </TabsTrigger>
                      <TabsTrigger value="history" className="flex-1">
                        <History aria-hidden /> History ({data.reviews.length})
                      </TabsTrigger>
                      {isAdmin && (
                        <TabsTrigger value="audit" className="flex-1">
                          <ScrollText aria-hidden /> Audit trail
                        </TabsTrigger>
                      )}
                    </TabsList>
                    <TabsContent value="review">
                      {canReview ? (
                        <ReviewForm caseId={id} aiGrade={p.isup_grade} />
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          Only pathologists and urologists can record a review decision.
                        </p>
                      )}
                    </TabsContent>
                    <TabsContent value="history">
                      <ReviewHistory reviews={data.reviews} aiGrade={p.isup_grade} />
                    </TabsContent>
                    {isAdmin && (
                      <TabsContent value="audit">
                        {audit.isLoading ? (
                          <Skeleton className="h-40" />
                        ) : (
                          <AuditList entries={audit.data ?? []} />
                        )}
                      </TabsContent>
                    )}
                  </Tabs>
                </CardContent>
              </Card>
            </>
          )}
          <p className="px-1 text-xs text-muted-foreground">
            Slide sha256 <span className="font-mono">{data.slide_sha256.slice(0, 16)}...</span>
            {data.predictions_count > 1 ? ` - ${data.predictions_count} AI runs (latest shown)` : ""}
          </p>
        </div>
      </div>
    </div>
  );
}
