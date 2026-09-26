import { ArrowLeft, ShieldAlert } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { DisclaimerBanner } from "@/components/app-shell";
import { FederatedDiagram } from "@/components/fl-diagram";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = { title: "About the model" };

const STEPS = [
  [
    "Find the tissue",
    "The lowest-resolution image of the slide is thresholded (Otsu on saturation) to separate tissue from glass, pen marks are removed.",
  ],
  [
    "Cut tiles",
    "Tissue is cut into 224 x 224-pixel tiles at full resolution (20x, ~0.5 um per pixel); up to 768 tiles per slide, chosen reproducibly.",
  ],
  [
    "Describe each tile",
    "Phikon, a pathology foundation model (a Vision Transformer trained on 40 million tissue tiles), turns every tile into 768 numbers.",
  ],
  [
    "Weigh the tiles",
    "A gated attention network learns which tiles matter most (these are the heatmap and the top tiles) and summarises the slide.",
  ],
  [
    "Grade",
    "An ordinal head estimates P(ISUP > 0), P(ISUP > 1), ... The 5 federated models' probabilities are averaged and turned into an ISUP grade with thresholds tuned on validation data.",
  ],
] as const;

export default function AboutPage() {
  return (
    <div className="min-h-screen">
      <DisclaimerBanner />
      <header className="border-b bg-card">
        <div className="container flex h-14 items-center justify-between">
          <Link href="/dashboard" className="flex items-center gap-3">
            <Logo />
            <span className="font-semibold">GleasonAI</span>
          </Link>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/dashboard">
              <ArrowLeft aria-hidden /> Back to the dashboard
            </Link>
          </Button>
        </div>
      </header>
      <main id="main" className="container max-w-4xl space-y-6 py-8">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">How GleasonAI works</h1>
          <p className="mt-2 text-muted-foreground">
            The telehealth review layer of the BS thesis{" "}
            <i>
              Federated Deep Learning for Prostate Cancer Gleason Grading via Histopathology Images and
              Telehealth Integration
            </i>{" "}
            (NUML, 2026).
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Trained with federated learning</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <FederatedDiagram />
            <p className="text-sm leading-relaxed text-muted-foreground">
              The model was trained on 10,614 prostate biopsy slides from two hospitals (PANDA challenge)
              without pooling the data: each hospital trained locally and only model weights were averaged
              (FedAvg, 40 rounds). Five models trained with different random seeds form the ensemble used
              here. On 2,124 held-out test slides it reached a csPCa AUC of 0.970, a cancer AUC of 0.994 and a
              quadratic weighted kappa of 0.905 against the reference grades.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>From slide to result</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="space-y-4">
              {STEPS.map(([title, text], i) => (
                <li key={title} className="flex gap-4">
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">
                    {i + 1}
                  </span>
                  <div>
                    <p className="font-medium">{title}</p>
                    <p className="text-sm text-muted-foreground">{text}</p>
                  </div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>

        <Card className="border-warn/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldAlert className="size-5 text-warn" aria-hidden /> Important
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm leading-relaxed">
            <p>
              <b>AI decision support only. Final diagnosis requires a pathologist.</b> Every result is
              provisional until a clinician reviews it; uncertain results are flagged for mandatory review.
            </p>
            <p>
              The model has only been validated on PANDA data from two hospitals. It is a research prototype,
              not a medical device: clinical use would need prospective validation, ethics approval and
              regulatory clearance. See the model card for full results and limitations.
            </p>
            <p>
              Slides never leave the server that runs the model; the browser only receives viewer tiles and
              results.
            </p>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
