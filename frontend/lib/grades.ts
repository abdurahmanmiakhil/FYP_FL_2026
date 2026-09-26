/** ISUP grade presentation: 0-1 green, 2-3 amber, 4-5 red - always with a text label. */

export type GradeBand = "low" | "mid" | "high";

export function gradeBand(g: number): GradeBand {
  if (g <= 1) return "low";
  if (g <= 3) return "mid";
  return "high";
}

export const BAND_LABEL: Record<GradeBand, string> = {
  low: "Low risk",
  mid: "Intermediate risk",
  high: "High risk",
};

export const GLEASON: Record<number, string> = {
  0: "Benign (no cancer)",
  1: "Gleason 3+3",
  2: "Gleason 3+4",
  3: "Gleason 4+3",
  4: "Gleason 4+4 / 3+5 / 5+3",
  5: "Gleason 4+5 / 5+4 / 5+5",
};

export const BAND_CLASSES: Record<GradeBand, { text: string; bg: string; border: string; fill: string }> = {
  low: { text: "text-grade-low", bg: "bg-grade-low-bg", border: "border-grade-low/40", fill: "bg-grade-low" },
  mid: { text: "text-grade-mid", bg: "bg-grade-mid-bg", border: "border-grade-mid/40", fill: "bg-grade-mid" },
  high: {
    text: "text-grade-high",
    bg: "bg-grade-high-bg",
    border: "border-grade-high/40",
    fill: "bg-grade-high",
  },
};

export const DECISION_LABEL = { confirmed: "Confirmed", amended: "Amended", rejected: "Rejected" } as const;

export const STAGE_LABEL: Record<string, string> = {
  uploaded: "Uploaded",
  queued: "Waiting in queue",
  "reading slide": "Reading slide",
  tiling: "Finding tissue",
  encoding: "Encoding tiles",
  predicting: "Running the 5 federated models",
  "saving results": "Saving results",
  retrying: "Retrying after a temporary error",
  done: "Done",
  failed: "Failed",
};

export const PIPELINE_STAGES = [
  "queued",
  "reading slide",
  "tiling",
  "encoding",
  "predicting",
  "saving results",
  "done",
];
