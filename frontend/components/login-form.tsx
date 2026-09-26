"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Clock, Info, Lock } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, toApiError } from "@/lib/api/client";
import { qk } from "@/lib/api/hooks";

const schema = z.object({
  email: z.string().trim().min(1, "Enter your email address").email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
  otp: z
    .string()
    .trim()
    .regex(/^\d{6,8}$/, "Enter the 6-digit code")
    .optional()
    .or(z.literal("")),
});
type FormValues = z.infer<typeof schema>;

const REASONS: Record<string, { icon: typeof Info; text: string }> = {
  idle: { icon: Clock, text: "You were signed out after 15 minutes of inactivity." },
  expired: { icon: Info, text: "Your session has ended. Please sign in again." },
  "signed-out": { icon: Info, text: "You have been signed out." },
};

function safeNext(next: string | null): string {
  // only same-site relative paths (no protocol-relative or absolute URLs)
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [error, setError] = useState<{ text: string; locked?: boolean } | null>(null);
  const [needOtp, setNeedOtp] = useState(false);
  const reason = REASONS[params.get("reason") ?? ""];

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "", otp: "" },
  });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    setError(null);
    const {
      data,
      error: body,
      response,
    } = await api.POST("/api/v1/auth/login", {
      body: { email: values.email, password: values.password, otp: values.otp || null },
    });
    if (response.ok && data) {
      qc.setQueryData(qk.me, data.user);
      router.replace(safeNext(params.get("next")));
      return;
    }
    const err = await toApiError(response, body);
    if (err.code === "otp_required") {
      setNeedOtp(true);
      setError({ text: err.message });
      return;
    }
    setError({ text: err.message, locked: response.status === 423 });
  });

  return (
    <form
      onSubmit={onSubmit}
      noValidate
      className="space-y-4"
      aria-describedby={error ? "login-error" : undefined}
    >
      {reason && !error && (
        <Alert variant="info">
          <reason.icon aria-hidden />
          <AlertDescription>{reason.text}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant={error.locked ? "warn" : "destructive"} id="login-error">
          {error.locked ? <Lock aria-hidden /> : <AlertCircle aria-hidden />}
          <AlertDescription>{error.text}</AlertDescription>
        </Alert>
      )}
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          autoComplete="username"
          autoFocus
          aria-invalid={!!errors.email}
          aria-describedby={errors.email ? "email-error" : undefined}
          {...form.register("email")}
        />
        {errors.email && (
          <p id="email-error" className="text-xs text-destructive">
            {errors.email.message}
          </p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          aria-invalid={!!errors.password}
          aria-describedby={errors.password ? "password-error" : undefined}
          {...form.register("password")}
        />
        {errors.password && (
          <p id="password-error" className="text-xs text-destructive">
            {errors.password.message}
          </p>
        )}
      </div>
      {needOtp && (
        <div className="space-y-2">
          <Label htmlFor="otp">Authenticator code</Label>
          <Input
            id="otp"
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="123456"
            autoFocus
            aria-invalid={!!errors.otp}
            {...form.register("otp")}
          />
          {errors.otp && <p className="text-xs text-destructive">{errors.otp.message}</p>}
        </div>
      )}
      <Button type="submit" className="w-full" loading={isSubmitting}>
        {isSubmitting ? "Signing in..." : "Sign in"}
      </Button>
      <p className="text-center text-xs text-muted-foreground">
        Accounts are created by your hospital administrator.
      </p>
    </form>
  );
}
