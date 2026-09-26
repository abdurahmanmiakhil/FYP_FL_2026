"use client";

import { ShieldCheck, ShieldX } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { actionLabel } from "@/components/audit-list";
import { PageHeader } from "@/components/clinical";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAudit, useVerifyAudit } from "@/lib/api/hooks";
import { formatDateTime } from "@/lib/format";

const ACTIONS = [
  "login",
  "login_failed",
  "logout",
  "upload",
  "view",
  "predict",
  "predict_failed",
  "review",
  "export",
  "delete",
  "user_create",
  "user_update",
  "password_reset",
  "retention_delete",
  "token_reuse",
];
const ALL = "all";

export default function AuditPage() {
  const [action, setAction] = useState<string>(ALL);
  const [page, setPage] = useState(1);
  const audit = useAudit({ action: action === ALL ? undefined : action, page });
  const verify = useVerifyAudit();
  const pages = audit.data ? Math.max(1, Math.ceil(audit.data.total / 50)) : 1;

  return (
    <>
      <PageHeader
        title="Audit trail"
        description="Append-only record of sign-ins, uploads, views, predictions, reviews, exports and deletions. Each entry is hash-chained to the previous one."
        actions={
          <Button variant="outline" loading={verify.isPending} onClick={() => verify.mutate()}>
            <ShieldCheck aria-hidden /> Verify integrity
          </Button>
        }
      />
      {verify.data && (
        <Alert variant={verify.data.ok ? "info" : "destructive"} className="mb-4">
          {verify.data.ok ? <ShieldCheck aria-hidden /> : <ShieldX aria-hidden />}
          <AlertDescription>
            {verify.data.ok
              ? `Hash chain intact: all ${String(verify.data.entries)} entries verified.`
              : `Chain broken at entry #${String(verify.data.first_broken_id)} - the log was modified outside the application.`}
          </AlertDescription>
        </Alert>
      )}
      <div className="mb-4 w-64 space-y-1.5">
        <Label htmlFor="action">Action</Label>
        <Select
          value={action}
          onValueChange={(v) => {
            setAction(v);
            setPage(1);
          }}
        >
          <SelectTrigger id="action">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All actions</SelectItem>
            {ACTIONS.map((a) => (
              <SelectItem key={a} value={a}>
                {actionLabel(a)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Card>
        {audit.isLoading ? (
          <Skeleton className="m-4 h-72" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>#</TableHead>
                <TableHead>Time</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Entity</TableHead>
                <TableHead>IP</TableHead>
                <TableHead>Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {audit.data?.items.map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="font-mono text-xs text-muted-foreground">{e.id}</TableCell>
                  <TableCell className="whitespace-nowrap">{formatDateTime(e.at)}</TableCell>
                  <TableCell>{e.user_email ?? "system"}</TableCell>
                  <TableCell className="font-medium">{actionLabel(e.action)}</TableCell>
                  <TableCell>
                    {e.entity === "case" && e.entity_id ? (
                      <Link href={`/cases/${e.entity_id}`} className="text-primary hover:underline">
                        case {e.entity_id.slice(0, 8)}
                      </Link>
                    ) : (
                      <span className="text-muted-foreground">
                        {e.entity}
                        {e.entity_id ? ` ${e.entity_id.slice(0, 8)}` : ""}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{e.ip ?? "-"}</TableCell>
                  <TableCell
                    className="max-w-xs truncate font-mono text-xs text-muted-foreground"
                    title={JSON.stringify(e.details)}
                  >
                    {Object.keys(e.details).length ? JSON.stringify(e.details) : ""}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <nav className="flex items-center justify-between border-t px-4 py-3 text-sm" aria-label="Pagination">
          <span className="text-muted-foreground">
            {audit.data?.total ?? 0} entries - page {page} of {pages}
          </span>
          <span className="flex gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </span>
        </nav>
      </Card>
    </>
  );
}
