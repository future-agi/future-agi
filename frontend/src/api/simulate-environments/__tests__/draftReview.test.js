import { describe, it, expect } from "vitest";
import { DraftReviewSchema } from "../draftReview.contract";
import { sampleDraftReview } from "../_fixtures/draftReview";

describe("draft review contract", () => {
  it("the sample review keeps to the backend's contract", () => {
    expect(DraftReviewSchema.safeParse(sampleDraftReview()).error?.issues).toBe(
      undefined,
    );
  });

  it("refuses what the backend's rules can't produce", () => {
    const review = sampleDraftReview();
    const rule = review.changes.find((one) => one.kind === "rule");
    const broken = {
      ...review,
      changes: review.changes.map((one) =>
        one === rule ? { ...one, depends_on: ["tool:issue_refund"] } : one,
      ),
    };
    expect(DraftReviewSchema.safeParse(broken).success).toBe(false);
  });
});
