"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, FileImage, ShieldCheck, UploadCloud, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { PageHeader } from "@/components/clinical";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { API_PREFIX, apiFetch, errorMessage, toApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { bytes } from "@/lib/format";
import { abortUpload, checkSlideFile, uploadResumable, type UploadProgress } from "@/lib/upload";
import { cn } from "@/lib/utils";

const schema = z.object({
  pseudonym_code: z
    .string()
    .trim()
    .regex(
      /^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$/,
      "2-64 letters, digits, '.', '_' or '-' (a pseudonym - never a name)",
    )
    .refine((v) => !/^\d{13}$|^\d{5}-\d{7}-\d$/.test(v), "Do not use national ID numbers"),
  hospital: z.string().trim().min(2, "Enter the hospital"),
  slide_id: z
    .string()
    .trim()
    .regex(/^[A-Za-z0-9_-]{0,64}$/, "Letters, digits, '_' or '-' only")
    .optional(),
});
type FormValues = z.infer<typeof schema>;

export default function NewCasePage() {
  const router = useRouter();
  const { user, hasRole } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploading" | "validating">("idle");
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const uploadId = useRef<string | null>(null);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { pseudonym_code: "", hospital: user?.hospital ?? "", slide_id: "" },
  });
  const { errors } = form.formState;

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    multiple: false,
    noClick: true,
    disabled: phase !== "idle",
    onDrop: (accepted) => {
      const f = accepted[0];
      if (!f) return;
      const problem = checkSlideFile(f);
      setFileError(problem);
      setFile(problem ? null : f);
    },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    if (!file) {
      setFileError("Choose a slide file first.");
      return;
    }
    setError(null);
    abort.current = new AbortController();
    setPhase("uploading");
    try {
      const id = await uploadResumable(file, { onProgress: setProgress, signal: abort.current.signal });
      uploadId.current = id;
      setPhase("validating");
      const res = await apiFetch(`${API_PREFIX}/uploads/${id}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          upload_id: id,
          pseudonym_code: values.pseudonym_code,
          hospital: hasRole("admin") ? values.hospital : null,
          slide_id: values.slide_id || null,
        }),
      });
      if (!res.ok) throw await toApiError(res);
      const created = (await res.json()) as { case_id: string; duplicate: boolean };
      if (created.duplicate) toast.info("This slide was uploaded before - opening the existing case.");
      else toast.success("Slide uploaded. AI analysis has started.");
      router.push(`/cases/${created.case_id}`);
    } catch (e) {
      if (abort.current?.signal.aborted) {
        if (uploadId.current) void abortUpload(uploadId.current);
        setError("Upload cancelled.");
      } else {
        setError(errorMessage(e));
      }
      setPhase("idle");
      setProgress(null);
    }
  });

  const percent = progress ? Math.round((progress.sent / Math.max(1, progress.total)) * 100) : 0;

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Upload a biopsy slide"
        description="Whole-slide image (.tif, .tiff or .svs, up to 2 GB). The slide stays on this server."
      />
      <form onSubmit={onSubmit} noValidate>
        <Card>
          <CardHeader>
            <CardTitle>Patient and slide</CardTitle>
            <CardDescription>
              Use the patient&apos;s pseudonym code only - never names or ID numbers.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {error && (
              <Alert variant="destructive">
                <AlertCircle aria-hidden />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="pseudonym_code">Patient pseudonym code</Label>
                <Input
                  id="pseudonym_code"
                  placeholder="e.g. PT-2026-0042"
                  autoComplete="off"
                  aria-invalid={!!errors.pseudonym_code}
                  aria-describedby="pseudonym-help"
                  disabled={phase !== "idle"}
                  {...form.register("pseudonym_code")}
                />
                <p
                  id="pseudonym-help"
                  className={cn(
                    "text-xs",
                    errors.pseudonym_code ? "text-destructive" : "text-muted-foreground",
                  )}
                >
                  {errors.pseudonym_code?.message ??
                    "The code your hospital uses to link this biopsy to the patient."}
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="hospital">Hospital</Label>
                <Input
                  id="hospital"
                  readOnly={!hasRole("admin")}
                  aria-readonly={!hasRole("admin")}
                  className={cn(!hasRole("admin") && "bg-muted")}
                  disabled={phase !== "idle"}
                  aria-invalid={!!errors.hospital}
                  {...form.register("hospital")}
                />
                <p className="text-xs text-muted-foreground">
                  {hasRole("admin")
                    ? "Administrators can upload for any hospital."
                    : "Cases are only visible to your hospital."}
                </p>
              </div>
            </div>
            <details className="rounded-md border p-3 text-sm">
              <summary className="cursor-pointer font-medium">Advanced: original slide ID (optional)</summary>
              <div className="mt-3 space-y-2">
                <Label htmlFor="slide_id">Slide ID</Label>
                <Input
                  id="slide_id"
                  placeholder="e.g. PANDA image_id"
                  disabled={phase !== "idle"}
                  {...form.register("slide_id")}
                />
                <p className={cn("text-xs", errors.slide_id ? "text-destructive" : "text-muted-foreground")}>
                  {errors.slide_id?.message ??
                    "Seeds the tile sampling. Enter the PANDA image_id to reproduce the thesis result for a PANDA slide; otherwise leave empty (the file's checksum is used)."}
                </p>
              </div>
            </details>

            <div
              {...getRootProps()}
              className={cn(
                "flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-8 text-center transition-colors",
                isDragActive ? "border-primary bg-primary/5" : "border-input",
                fileError && "border-destructive",
              )}
            >
              <input {...getInputProps()} aria-label="Slide file" />
              {file ? (
                <>
                  <FileImage className="size-10 text-primary" aria-hidden />
                  <div>
                    <p className="font-medium">{file.name}</p>
                    <p className="text-sm text-muted-foreground">{bytes(file.size)}</p>
                  </div>
                  {phase === "idle" && (
                    <Button type="button" variant="ghost" size="sm" onClick={() => setFile(null)}>
                      <X aria-hidden /> Choose another file
                    </Button>
                  )}
                </>
              ) : (
                <>
                  <UploadCloud className="size-10 text-muted-foreground" aria-hidden />
                  <p className="font-medium">Drag and drop the slide here</p>
                  <Button type="button" variant="outline" onClick={open}>
                    Browse files
                  </Button>
                  <p className="text-xs text-muted-foreground">.tif, .tiff or .svs - up to 2 GB</p>
                </>
              )}
              {fileError && (
                <p className="text-sm font-medium text-destructive" role="alert">
                  {fileError}
                </p>
              )}
            </div>

            {phase !== "idle" && (
              <div className="space-y-2" aria-live="polite">
                <div className="flex justify-between text-sm">
                  <span>
                    {phase === "uploading"
                      ? "Uploading (resumes automatically if the connection drops)"
                      : "Checking the slide..."}
                  </span>
                  <span className="font-mono tabular-nums">
                    {progress ? `${bytes(progress.sent)} / ${bytes(progress.total)} (${percent}%)` : ""}
                  </span>
                </div>
                <Progress value={phase === "validating" ? 100 : percent} aria-label="Upload progress" />
              </div>
            )}

            <div className="flex items-start gap-2 rounded-md bg-muted p-3 text-xs text-muted-foreground">
              <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
              The file is checked (format, size, readable pyramid, optional antivirus), stored under a random
              name and analysed on this server. Your browser only receives viewer tiles and results.
            </div>

            <div className="flex justify-end gap-2">
              {phase === "uploading" ? (
                <Button type="button" variant="outline" onClick={() => abort.current?.abort()}>
                  Cancel upload
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => router.back()}
                  disabled={phase !== "idle"}
                >
                  Cancel
                </Button>
              )}
              <Button type="submit" loading={phase !== "idle"} disabled={!file}>
                Upload and analyse
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}
