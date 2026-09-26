"use client";

import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Download,
  FileStack,
  Search,
  Upload,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { DecisionBadge, EmptyState, GradeBadge, PageHeader, StatusChip } from "@/components/clinical";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { CaseStatus } from "@/lib/api/client";
import { type CaseQuery, type CaseSort, useCases } from "@/lib/api/hooks";
import { useAuth } from "@/lib/auth";
import { formatDateTime, pct } from "@/lib/format";

const PAGE_SIZE = 20;
const ANY = "any";

function useQueryState(): [CaseQuery, (patch: Partial<CaseQuery>) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const query = useMemo<CaseQuery>(() => {
    const bool = (k: string) =>
      params.get(k) === "true" ? true : params.get(k) === "false" ? false : undefined;
    const num = (k: string) => (params.get(k) != null ? Number(params.get(k)) : undefined);
    return {
      q: params.get("q") ?? undefined,
      status: (params.get("status") as CaseStatus | null) ?? undefined,
      grade: num("grade"),
      needs_review: bool("needs_review"),
      low_confidence: bool("low_confidence"),
      date_from: params.get("date_from") ?? undefined,
      date_to: params.get("date_to") ?? undefined,
      sort: (params.get("sort") as CaseSort | null) ?? "-created_at",
      page: num("page") ?? 1,
      page_size: PAGE_SIZE,
    };
  }, [params]);
  const update = (patch: Partial<CaseQuery>) => {
    const next = new URLSearchParams(params.toString());
    const merged = { ...query, ...patch, page: patch.page ?? 1 };
    for (const [k, v] of Object.entries(merged)) {
      if (
        k === "page_size" ||
        v === undefined ||
        v === "" ||
        (k === "page" && v === 1) ||
        (k === "sort" && v === "-created_at")
      )
        next.delete(k);
      else next.set(k, String(v));
    }
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };
  return [query, update];
}

function SortHeader({
  label,
  field,
  query,
  onSort,
}: {
  label: string;
  field: string;
  query: CaseQuery;
  onSort: (s: CaseSort) => void;
}) {
  const active = query.sort === field || query.sort === `-${field}`;
  const desc = query.sort === `-${field}`;
  const Icon = !active ? ArrowUpDown : desc ? ArrowDown : ArrowUp;
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 uppercase hover:text-foreground"
      onClick={() => onSort((active && !desc ? `-${field}` : field) as CaseSort)}
      aria-label={`Sort by ${label}`}
    >
      {label}
      <Icon className="size-3.5" aria-hidden />
    </button>
  );
}

function CasesTable() {
  const [query, update] = useQueryState();
  const { hasRole } = useAuth();
  const { data, isLoading, isFetching, error } = useCases(query);
  const [search, setSearch] = useState(query.q ?? "");

  useEffect(() => {
    const t = setTimeout(() => {
      if ((query.q ?? "") !== search) update({ q: search || undefined });
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const exportUrl = useMemo(() => {
    const p = new URLSearchParams();
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && k !== "page" && k !== "page_size") p.set(k, String(v));
    });
    return `/api/v1/cases/export.csv?${p.toString()}`;
  }, [query]);

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const filtered = !!(
    query.q ||
    query.status ||
    query.grade != null ||
    query.needs_review != null ||
    query.low_confidence != null ||
    query.date_from ||
    query.date_to
  );

  return (
    <>
      <PageHeader
        title="Cases"
        description={
          data
            ? `${data.total} case${data.total === 1 ? "" : "s"}${filtered ? " match the filters" : ""}`
            : " "
        }
        actions={
          <>
            <Button variant="outline" asChild>
              <a href={exportUrl} download>
                <Download aria-hidden /> Export CSV
              </a>
            </Button>
            {hasRole("pathologist", "urologist", "admin") && (
              <Button asChild>
                <Link href="/cases/new">
                  <Upload aria-hidden /> Upload slide
                </Link>
              </Button>
            )}
          </>
        }
      />

      <Card className="mb-4">
        <CardContent className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-7">
          <div className="space-y-1.5 lg:col-span-2">
            <Label htmlFor="search">Patient code</Label>
            <div className="relative">
              <Search
                className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                id="search"
                className="pl-9"
                placeholder="Search..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="status">Status</Label>
            <Select
              value={query.status ?? ANY}
              onValueChange={(v) => update({ status: v === ANY ? undefined : (v as CaseStatus) })}
            >
              <SelectTrigger id="status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Any status</SelectItem>
                <SelectItem value="queued">Queued</SelectItem>
                <SelectItem value="processing">Processing</SelectItem>
                <SelectItem value="done">Result ready</SelectItem>
                <SelectItem value="failed">Failed</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="grade">AI grade</Label>
            <Select
              value={query.grade != null ? String(query.grade) : ANY}
              onValueChange={(v) => update({ grade: v === ANY ? undefined : Number(v) })}
            >
              <SelectTrigger id="grade">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Any grade</SelectItem>
                {[0, 1, 2, 3, 4, 5].map((g) => (
                  <SelectItem key={g} value={String(g)}>
                    ISUP {g}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="review">Review</Label>
            <Select
              value={
                query.needs_review === true
                  ? "needs"
                  : query.needs_review === false
                    ? "reviewed"
                    : query.low_confidence
                      ? "low"
                      : ANY
              }
              onValueChange={(v) =>
                update({
                  needs_review: v === "needs" ? true : v === "reviewed" ? false : undefined,
                  low_confidence: v === "low" ? true : undefined,
                })
              }
            >
              <SelectTrigger id="review">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>All</SelectItem>
                <SelectItem value="needs">Needs review</SelectItem>
                <SelectItem value="low">Low confidence</SelectItem>
                <SelectItem value="reviewed">Reviewed</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="from">From</Label>
            <Input
              id="from"
              type="date"
              value={query.date_from ?? ""}
              onChange={(e) => update({ date_from: e.target.value || undefined })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="to">To</Label>
            <Input
              id="to"
              type="date"
              value={query.date_to ?? ""}
              onChange={(e) => update({ date_to: e.target.value || undefined })}
            />
          </div>
          {filtered && (
            <div className="sm:col-span-2 lg:col-span-7">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  update({
                    q: undefined,
                    status: undefined,
                    grade: undefined,
                    needs_review: undefined,
                    low_confidence: undefined,
                    date_from: undefined,
                    date_to: undefined,
                  });
                }}
              >
                <X aria-hidden /> Clear filters
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        {error ? (
          <p className="p-6 text-sm text-destructive">Could not load cases.</p>
        ) : isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : !data?.items.length ? (
          <div className="p-6">
            <EmptyState icon={FileStack} title={filtered ? "No cases match these filters" : "No cases yet"} />
          </div>
        ) : (
          <Table aria-busy={isFetching}>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>
                  <SortHeader
                    label="Patient"
                    field="patient_code"
                    query={query}
                    onSort={(s) => update({ sort: s })}
                  />
                </TableHead>
                <TableHead>
                  <SortHeader
                    label="Uploaded"
                    field="created_at"
                    query={query}
                    onSort={(s) => update({ sort: s })}
                  />
                </TableHead>
                <TableHead>Status</TableHead>
                <TableHead>
                  <SortHeader
                    label="ISUP"
                    field="isup_grade"
                    query={query}
                    onSort={(s) => update({ sort: s })}
                  />
                </TableHead>
                <TableHead>
                  <SortHeader
                    label="P(csPCa)"
                    field="p_cspca"
                    query={query}
                    onSort={(s) => update({ sort: s })}
                  />
                </TableHead>
                <TableHead>Review</TableHead>
                {hasRole("admin") && <TableHead>Hospital</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((c) => (
                <TableRow key={c.id}>
                  <TableCell>
                    <Link href={`/cases/${c.id}`} className="font-medium text-primary hover:underline">
                      {c.patient_code}
                    </Link>
                    {c.low_confidence && (
                      <span
                        className="ml-2 inline-flex items-center gap-1 text-xs text-warn"
                        title="Low confidence - mandatory review"
                      >
                        <AlertTriangle className="size-3.5" aria-hidden />
                        <span className="sr-only">Low confidence</span>
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {formatDateTime(c.created_at)}
                  </TableCell>
                  <TableCell>
                    {c.status === "failed" && c.error ? (
                      <span title={c.error}>
                        <StatusChip status={c.status} />
                      </span>
                    ) : (
                      <StatusChip
                        status={c.status}
                        stage={c.progress_stage}
                        done={c.progress_done}
                        total={c.progress_total}
                      />
                    )}
                  </TableCell>
                  <TableCell>
                    <GradeBadge grade={c.isup_grade} />
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">{pct(c.p_cspca)}</TableCell>
                  <TableCell>
                    {c.status === "done" ? (
                      <span className="flex flex-col gap-0.5">
                        <DecisionBadge decision={c.review_decision} />
                        {c.reviewer_name && (
                          <span className="text-xs text-muted-foreground">{c.reviewer_name}</span>
                        )}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  {hasRole("admin") && <TableCell className="text-muted-foreground">{c.hospital}</TableCell>}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {data && data.total > PAGE_SIZE && (
          <nav
            className="flex items-center justify-between border-t px-4 py-3 text-sm"
            aria-label="Pagination"
          >
            <span className="text-muted-foreground">
              Page {query.page} of {pages}
            </span>
            <span className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={(query.page ?? 1) <= 1}
                onClick={() => update({ page: (query.page ?? 1) - 1 })}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={(query.page ?? 1) >= pages}
                onClick={() => update({ page: (query.page ?? 1) + 1 })}
              >
                Next
              </Button>
            </span>
          </nav>
        )}
      </Card>
    </>
  );
}

export default function CasesPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <CasesTable />
    </Suspense>
  );
}
