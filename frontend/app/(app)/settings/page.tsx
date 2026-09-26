"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, LogOut, Monitor, Moon, ShieldCheck, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { PageHeader } from "@/components/clinical";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/client";
import { qk, useChangePassword, useTotpSetup, useTotpToggle } from "@/lib/api/hooks";
import { useAuth } from "@/lib/auth";
import { passwordSchema } from "@/lib/validation";

const pwSchema = z
  .object({
    current_password: z.string().min(1, "Enter your current password"),
    new_password: passwordSchema,
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, { path: ["confirm"], message: "Passwords do not match" });
type PwValues = z.infer<typeof pwSchema>;

function PasswordCard() {
  const change = useChangePassword();
  const qc = useQueryClient();
  const form = useForm<PwValues>({
    resolver: zodResolver(pwSchema),
    defaultValues: { current_password: "", new_password: "", confirm: "" },
  });
  const { errors } = form.formState;
  const submit = form.handleSubmit((v) =>
    change.mutate(
      { current_password: v.current_password, new_password: v.new_password },
      {
        onSuccess: () => {
          toast.success("Password changed. Your other sessions were signed out.");
          form.reset();
          void qc.invalidateQueries({ queryKey: qk.me });
        },
        onError: (e) => toast.error(errorMessage(e)),
      },
    ),
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-4" aria-hidden /> Change password
        </CardTitle>
        <CardDescription>
          At least 12 characters with three of: lower case, upper case, digit, symbol.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} noValidate className="space-y-3">
          {(
            [
              ["current_password", "Current password", "current-password"],
              ["new_password", "New password", "new-password"],
              ["confirm", "Repeat new password", "new-password"],
            ] as const
          ).map(([name, label, ac]) => (
            <div key={name} className="space-y-1.5">
              <Label htmlFor={name}>{label}</Label>
              <Input
                id={name}
                type="password"
                autoComplete={ac}
                aria-invalid={!!errors[name]}
                {...form.register(name)}
              />
              {errors[name] && <p className="text-xs text-destructive">{errors[name]?.message}</p>}
            </div>
          ))}
          <Button type="submit" loading={change.isPending}>
            Change password
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function TwoFactorCard() {
  const { user } = useAuth();
  const setup = useTotpSetup();
  const enable = useTotpToggle("enable");
  const disable = useTotpToggle("disable");
  const [code, setCode] = useState("");
  const enabled = user?.totp_enabled;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" aria-hidden /> Two-factor authentication
          {enabled ? <Badge variant="success">On</Badge> : <Badge variant="secondary">Off</Badge>}
        </CardTitle>
        <CardDescription>
          A code from an authenticator app (Google Authenticator, Microsoft Authenticator...) is required at
          sign-in.
          {user?.role === "admin" && !enabled && " Strongly recommended for administrators."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!enabled && !setup.data && (
          <Button
            variant="outline"
            loading={setup.isPending}
            onClick={() => setup.mutate(undefined, { onError: (e) => toast.error(errorMessage(e)) })}
          >
            Set up two-factor
          </Button>
        )}
        {!enabled && setup.data && (
          <div className="space-y-3">
            <p className="text-sm">1. Scan this QR code with your authenticator app:</p>
            {/* eslint-disable-next-line @next/next/no-img-element -- generated QR code */}
            <img
              src={`data:image/svg+xml;utf8,${encodeURIComponent(setup.data.qr_svg)}`}
              alt="QR code for the authenticator app"
              className="size-44 rounded border bg-white p-2"
            />
            <p className="text-xs text-muted-foreground">
              Or enter this key manually: <span className="break-all font-mono">{setup.data.secret}</span>
            </p>
          </div>
        )}
        {(setup.data || enabled) && (
          <form
            className="flex max-w-sm items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const m = enabled ? disable : enable;
              m.mutate(code, {
                onSuccess: () => {
                  toast.success(enabled ? "Two-factor turned off." : "Two-factor is now on.");
                  setCode("");
                  setup.reset();
                },
                onError: (err) => toast.error(errorMessage(err)),
              });
            }}
          >
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="totp-code">
                {enabled ? "Code (to turn off)" : "2. Enter the 6-digit code"}
              </Label>
              <Input
                id="totp-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </div>
            <Button
              type="submit"
              variant={enabled ? "outline" : "default"}
              loading={enable.isPending || disable.isPending}
              disabled={code.length < 6}
            >
              {enabled ? "Turn off" : "Turn on"}
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  return (
    <div className="max-w-3xl">
      <PageHeader
        title="Account settings"
        description={`${user?.email ?? ""} - ${user?.role ?? ""} at ${user?.hospital ?? ""}`}
      />
      {user?.must_change_password && (
        <Alert variant="warn" className="mb-4">
          <KeyRound aria-hidden />
          <AlertDescription>
            Your password was set by an administrator. Please choose your own password now.
          </AlertDescription>
        </Alert>
      )}
      <div className="space-y-4">
        <PasswordCard />
        <TwoFactorCard />
        <Card>
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
          </CardHeader>
          <CardContent className="flex gap-2" role="radiogroup" aria-label="Theme">
            {(
              [
                ["light", Sun, "Light"],
                ["dark", Moon, "Dark"],
                ["system", Monitor, "System"],
              ] as const
            ).map(([v, Icon, label]) => (
              <Button
                key={v}
                variant={theme === v ? "default" : "outline"}
                role="radio"
                aria-checked={theme === v}
                onClick={() => setTheme(v)}
              >
                <Icon aria-hidden /> {label}
              </Button>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Sessions</CardTitle>
            <CardDescription>
              You are signed out automatically after 15 minutes without activity.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={() => void logout({ everywhere: true })}>
              <LogOut aria-hidden /> Sign out on all devices
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
