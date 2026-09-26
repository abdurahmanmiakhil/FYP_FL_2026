import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ClassProbabilityChart, GradeBadge, ProbabilityBar, StatusChip } from "@/components/clinical";
import { TooltipProvider } from "@/components/ui/tooltip";
import { reviewSchema } from "@/components/review-panel";
import { gradeBand } from "@/lib/grades";
import { passwordSchema } from "@/lib/validation";

describe("grades", () => {
  it("maps ISUP to bands", () => {
    expect([0, 1, 2, 3, 4, 5].map(gradeBand)).toEqual(["low", "low", "mid", "mid", "high", "high"]);
  });

  it("never shows a grade by colour alone", () => {
    render(<GradeBadge grade={4} size="lg" />);
    expect(screen.getByText("ISUP 4")).toBeInTheDocument();
    expect(screen.getByText("High risk")).toBeInTheDocument();
  });
});

describe("ProbabilityBar", () => {
  it("exposes the value and threshold markers accessibly", () => {
    render(
      <TooltipProvider>
        <ProbabilityBar label="P(csPCa)" value={0.2234} markers={[{ value: 0.481, label: "Youden" }]} />
      </TooltipProvider>,
    );
    expect(screen.getByRole("meter", { name: "P(csPCa)" })).toHaveAttribute("aria-valuenow", "22.3");
    expect(screen.getByLabelText(/Youden threshold 48.1%/)).toBeInTheDocument();
  });
});

describe("ClassProbabilityChart", () => {
  it("labels every class with its probability", () => {
    render(<ClassProbabilityChart probs={[0.6, 0.2, 0.1, 0.05, 0.03, 0.02]} predicted={1} />);
    expect(screen.getByLabelText("ISUP 0: 60.0%")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
  });
});

describe("StatusChip", () => {
  it("shows encoding progress", () => {
    render(<StatusChip status="processing" stage="encoding" done={64} total={112} />);
    expect(screen.getByText(/Encoding tiles/)).toHaveTextContent("64/112");
  });
});

describe("review validation", () => {
  it("requires a grade when amending and a comment unless confirming", () => {
    expect(reviewSchema.safeParse({ decision: "confirmed", final_isup: null, comment: "" }).success).toBe(
      true,
    );
    const amend = reviewSchema.safeParse({ decision: "amended", final_isup: null, comment: "" });
    expect(amend.success).toBe(false);
    expect(amend.error?.issues.map((i) => i.path[0])).toEqual(["final_isup", "comment"]);
    expect(
      reviewSchema.safeParse({ decision: "rejected", final_isup: null, comment: "blurred" }).success,
    ).toBe(true);
  });

  it("enforces the password policy", () => {
    expect(passwordSchema.safeParse("short").success).toBe(false);
    expect(passwordSchema.safeParse("alllowercaseletters").success).toBe(false);
    expect(passwordSchema.safeParse("Correct-Horse-42").success).toBe(true);
  });
});
