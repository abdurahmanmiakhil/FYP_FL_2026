"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, PencilLine, XCircle } from "lucide-react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { DecisionBadge, GradeBadge } from "@/components/clinical";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage, type Review } from "@/lib/api/client";
import { useCreateReview } from "@/lib/api/hooks";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { GLEASON } from "@/lib/grades";
import { cn } from "@/lib/utils";

export const reviewSchema = z
  .object({
    decision: z.enum(["confirmed", "amended", "rejected"], { required_error: "Choose a decision" }),
    final_isup: z.number().int().min(0).max(5).nullable(),
    comment: z.string().max(4000, "At most 4000 characters"),
  })
  .superRefine((v, ctx) => {
    if (v.decision === "amended" && v.final_isup == null)
      ctx.addIssue({ code: "custom", path: ["final_isup"], message: "Choose the corrected ISUP grade" });
    if (v.decision !== "confirmed" && !v.comment.trim())
      ctx.addIssue({
        code: "custom",
        path: ["comment"],
        message: "A comment is required when amending or rejecting",
      });
  });
export type ReviewValues = z.infer<typeof reviewSchema>;

const OPTIONS = [
  { value: "confirmed", label: "Confirm", icon: CheckCircle2, hint: "The AI grade is correct" },
  { value: "amended", label: "Amend grade", icon: PencilLine, hint: "Correct the ISUP grade" },
  { value: "rejected", label: "Reject", icon: XCircle, hint: "Result not usable" },
] as const;

export function ReviewForm({ caseId, aiGrade }: { caseId: string; aiGrade: number }) {
  const { user } = useAuth();
  const mutation = useCreateReview(caseId, user);
  const form = useForm<ReviewValues>({
    resolver: zodResolver(reviewSchema),
    defaultValues: { decision: undefined, final_isup: null, comment: "" },
  });
  const decision = form.watch("decision");
  const { errors } = form.formState;

  const onSubmit = form.handleSubmit((v) => {
    mutation.mutate(
      {
        decision: v.decision,
        final_isup: v.decision === "amended" ? v.final_isup : null,
        comment: v.comment.trim() || null,
      },
      {
        onSuccess: () => {
          toast.success("Review recorded in the audit trail.");
          form.reset({ decision: undefined, final_isup: null, comment: "" });
        },
        onError: (e) => toast.error(errorMessage(e)),
      },
    );
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4" aria-label="Clinician review">
      <fieldset>
        <legend className="mb-2 text-sm font-medium">Decision</legend>
        <Controller
          control={form.control}
          name="decision"
          render={({ field }) => (
            <div role="radiogroup" aria-label="Decision" className="grid grid-cols-3 gap-2">
              {OPTIONS.map((o) => {
                const selected = field.value === o.value;
                return (
                  <button
                    key={o.value}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => field.onChange(o.value)}
                    className={cn(
                      "flex flex-col items-center gap-1 rounded-md border px-2 py-3 text-sm font-medium transition-colors",
                      selected
                        ? "border-primary bg-primary/10 text-primary ring-1 ring-primary"
                        : "hover:bg-accent",
                    )}
                  >
                    <o.icon className="size-5" aria-hidden />
                    {o.label}
                    <span className="text-center text-[11px] font-normal text-muted-foreground">
                      {o.hint}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        />
        {errors.decision && <p className="mt-1 text-xs text-destructive">{errors.decision.message}</p>}
      </fieldset>

      {decision === "amended" && (
        <div className="space-y-2">
          <Label htmlFor="final_isup">Corrected ISUP grade (AI: {aiGrade})</Label>
          <Controller
            control={form.control}
            name="final_isup"
            render={({ field }) => (
              <Select
                value={field.value == null ? "" : String(field.value)}
                onValueChange={(v) => field.onChange(Number(v))}
              >
                <SelectTrigger id="final_isup" aria-invalid={!!errors.final_isup}>
                  <SelectValue placeholder="Choose grade" />
                </SelectTrigger>
                <SelectContent>
                  {[0, 1, 2, 3, 4, 5].map((g) => (
                    <SelectItem key={g} value={String(g)} disabled={g === aiGrade}>
                      ISUP {g} - {GLEASON[g]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          />
          {errors.final_isup && <p className="text-xs text-destructive">{errors.final_isup.message}</p>}
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="comment">
          Comment{" "}
          {decision && decision !== "confirmed" ? (
            <span className="text-destructive">(required)</span>
          ) : (
            "(optional)"
          )}
        </Label>
        <Textarea
          id="comment"
          placeholder={
            decision === "confirmed" || !decision
              ? "Optional notes..."
              : "Explain the amendment or rejection..."
          }
          aria-invalid={!!errors.comment}
          {...form.register("comment")}
        />
        {errors.comment && <p className="text-xs text-destructive">{errors.comment.message}</p>}
      </div>

      <Button type="submit" className="w-full" loading={mutation.isPending}>
        Submit review
      </Button>
    </form>
  );
}

export function ReviewHistory({ reviews, aiGrade }: { reviews: Review[]; aiGrade: number | null }) {
  if (!reviews.length)
    return <p className="text-sm text-muted-foreground">No reviews yet. The AI result is provisional.</p>;
  return (
    <ol className="space-y-3">
      {reviews.map((r, i) => (
        <li
          key={r.id}
          className={cn("rounded-md border p-3 text-sm", i === 0 && "border-primary/40 bg-primary/5")}
        >
          <div className="flex flex-wrap items-center gap-2">
            <DecisionBadge decision={r.decision} />
            {r.final_isup != null && (
              <span className="flex items-center gap-1.5">
                final <GradeBadge grade={r.final_isup} />
              </span>
            )}
            {r.decision === "amended" && aiGrade != null && (
              <span className="text-xs text-muted-foreground">(AI: ISUP {aiGrade})</span>
            )}
            {i === 0 && <span className="ml-auto text-xs font-medium text-primary">Current</span>}
          </div>
          {r.comment && <p className="mt-2 whitespace-pre-wrap">{r.comment}</p>}
          <p className="mt-2 text-xs text-muted-foreground">
            {r.reviewer_name ?? "Unknown"} {r.reviewer_role ? `(${r.reviewer_role})` : ""} -{" "}
            {formatDateTime(r.created_at)}
          </p>
        </li>
      ))}
    </ol>
  );
}
