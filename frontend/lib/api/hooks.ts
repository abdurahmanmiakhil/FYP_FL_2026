"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  unwrap,
  type CaseDetail,
  type CaseStatus,
  type Decision,
  type Review,
  type Role,
  type Schemas,
} from "./client";

export const qk = {
  me: ["me"] as const,
  stats: ["stats"] as const,
  cases: (p: CaseQuery) => ["cases", p] as const,
  case: (id: string) => ["case", id] as const,
  caseAudit: (id: string) => ["case", id, "audit"] as const,
  users: (p: object) => ["users", p] as const,
  audit: (p: object) => ["audit", p] as const,
  model: ["model"] as const,
  health: ["health"] as const,
};

export type CaseSort =
  "-created_at" | "created_at" | "-isup_grade" | "isup_grade" | "-p_cspca" | "p_cspca" | "patient_code";

export interface CaseQuery {
  status?: CaseStatus;
  grade?: number;
  date_from?: string;
  date_to?: string;
  needs_review?: boolean;
  low_confidence?: boolean;
  q?: string;
  sort?: CaseSort;
  page?: number;
  page_size?: number;
}

const ACTIVE: CaseStatus[] = ["uploaded", "queued", "processing"];

export function useStats() {
  return useQuery({
    queryKey: qk.stats,
    queryFn: () => unwrap(api.GET("/api/v1/stats")),
    refetchInterval: 30_000,
  });
}

export function useCases(query: CaseQuery) {
  return useQuery({
    queryKey: qk.cases(query),
    queryFn: () => unwrap(api.GET("/api/v1/cases", { params: { query } })),
    placeholderData: keepPreviousData,
    // keep status chips live while any listed case is still being processed
    refetchInterval: (q) => (q.state.data?.items.some((c) => ACTIVE.includes(c.status)) ? 3000 : false),
  });
}

export function useCase(id: string) {
  return useQuery({
    queryKey: qk.case(id),
    queryFn: () => unwrap(api.GET("/api/v1/cases/{case_id}", { params: { path: { case_id: id } } })),
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 5000 : false),
  });
}

export function useCaseAudit(id: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.caseAudit(id),
    queryFn: () => unwrap(api.GET("/api/v1/cases/{case_id}/audit", { params: { path: { case_id: id } } })),
    enabled,
  });
}

export interface ReviewInput {
  decision: Decision;
  final_isup?: number | null;
  comment?: string | null;
}

/** Optimistic: the review appears in the history immediately and rolls back on error. */
export function useCreateReview(caseId: string, me: { full_name: string; role: Role } | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ReviewInput) =>
      unwrap(api.POST("/api/v1/cases/{case_id}/reviews", { params: { path: { case_id: caseId } }, body })),
    onMutate: async (body) => {
      await qc.cancelQueries({ queryKey: qk.case(caseId) });
      const prev = qc.getQueryData<CaseDetail>(qk.case(caseId));
      if (prev) {
        const optimistic: Review = {
          id: `optimistic-${Date.now()}`,
          case_id: caseId,
          prediction_id: prev.prediction?.id ?? null,
          decision: body.decision,
          final_isup:
            body.decision === "confirmed" ? (prev.prediction?.isup_grade ?? null) : (body.final_isup ?? null),
          comment: body.comment ?? null,
          created_at: new Date().toISOString(),
          reviewer_name: me?.full_name ?? null,
          reviewer_role: me?.role ?? null,
        };
        qc.setQueryData<CaseDetail>(qk.case(caseId), {
          ...prev,
          reviews: [optimistic, ...prev.reviews],
          review_decision: body.decision,
          final_isup: optimistic.final_isup,
          prediction: prev.prediction ? { ...prev.prediction, status: "reviewed" } : prev.prediction,
        });
      }
      return { prev };
    },
    onError: (_e, _b, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.case(caseId), ctx.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.case(caseId) });
      void qc.invalidateQueries({ queryKey: ["cases"] });
      void qc.invalidateQueries({ queryKey: qk.stats });
    },
  });
}

export function useRetryCase(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/cases/{case_id}/retry", { params: { path: { case_id: caseId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.case(caseId) }),
  });
}

export function useDeleteCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (caseId: string) =>
      unwrap(api.DELETE("/api/v1/cases/{case_id}", { params: { path: { case_id: caseId } } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cases"] });
      void qc.invalidateQueries({ queryKey: qk.stats });
    },
  });
}

// ---------------------------------------------------------------- admin

export function useUsers(params: { q?: string; role?: Role; page?: number }) {
  return useQuery({
    queryKey: qk.users(params),
    queryFn: () => unwrap(api.GET("/api/v1/users", { params: { query: { ...params, page_size: 50 } } })),
    placeholderData: keepPreviousData,
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["UserCreate"]) => unwrap(api.POST("/api/v1/users", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas["UserUpdate"] }) =>
      unwrap(api.PATCH("/api/v1/users/{user_id}", { params: { path: { user_id: id } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) =>
      unwrap(
        api.POST("/api/v1/users/{user_id}/reset-password", {
          params: { path: { user_id: id } },
          body: { new_password: password },
        }),
      ),
  });
}

export function useAudit(params: { action?: string; entity_id?: string; page?: number }) {
  return useQuery({
    queryKey: qk.audit(params),
    queryFn: () =>
      unwrap(api.GET("/api/v1/admin/audit", { params: { query: { ...params, page_size: 50 } } })),
    placeholderData: keepPreviousData,
  });
}

export function useVerifyAudit() {
  return useMutation({ mutationFn: () => unwrap(api.GET("/api/v1/admin/audit/verify")) });
}

export function useModelCard() {
  return useQuery({ queryKey: qk.model, queryFn: () => unwrap(api.GET("/api/v1/admin/model")) });
}

export function useHealth() {
  return useQuery({
    queryKey: qk.health,
    // /ready answers 503 with the same body when models are not loaded: read it either way
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/ready");
      return (data ?? error) as Schemas["Health"];
    },
    refetchInterval: 15_000,
  });
}

// ---------------------------------------------------------------- account

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: Schemas["PasswordChangeIn"]) => unwrap(api.POST("/api/v1/auth/password", { body })),
  });
}

export function useTotpSetup() {
  return useMutation({ mutationFn: () => unwrap(api.POST("/api/v1/auth/totp/setup")) });
}

export function useTotpToggle(kind: "enable" | "disable") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (code: string) =>
      unwrap(
        api.POST(kind === "enable" ? "/api/v1/auth/totp/enable" : "/api/v1/auth/totp/disable", {
          body: { code },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.me }),
  });
}
