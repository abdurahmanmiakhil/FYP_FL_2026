"use client";

import { AlertTriangle } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";

/** Error boundary for every page: never shows stack traces or patient data. */
export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <main
      className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-6 text-center"
      role="alert"
    >
      <AlertTriangle className="size-10 text-destructive" aria-hidden />
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="max-w-md text-muted-foreground">
        The page could not be displayed. Try again; if the problem continues, contact your administrator
        {error.digest ? ` (reference ${error.digest})` : ""}.
      </p>
      <Button onClick={reset}>Try again</Button>
    </main>
  );
}
