import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { LoginForm } from "@/components/login-form";
import { Logo } from "@/components/logo";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-4 py-10">
      <div className="w-full max-w-sm space-y-6 rounded-xl border bg-card p-8 shadow-sm">
        <div className="flex flex-col items-center gap-3 text-center">
          <Logo className="size-12" />
          <div>
            <h1 className="text-xl font-semibold">GleasonAI</h1>
            <p className="text-sm text-muted-foreground">Prostate biopsy AI review - clinician sign in</p>
          </div>
        </div>
        <Suspense>
          <LoginForm />
        </Suspense>
      </div>
      <p className="max-w-sm text-center text-xs text-muted-foreground">
        AI decision support only. Final diagnosis requires a pathologist. Research prototype -{" "}
        <Link href="/about" className="underline underline-offset-2 hover:text-foreground">
          how it works
        </Link>
        .
      </p>
    </main>
  );
}
