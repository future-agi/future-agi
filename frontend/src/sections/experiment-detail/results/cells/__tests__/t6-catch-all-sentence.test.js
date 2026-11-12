// An unexpected error stores the server's catch-all text (error_codes.py
// FAILED_TO_PROCESS_EVALUATION, FAILED_TO_PROCESS_ROW). After "The
// evaluation failed:" it read "The evaluation failed: Evaluation failed.
// Please contact Future AGI support." It adds nothing, so like the other
// generic texts it shows no sentence and the cell keeps "the evaluation
// failed".
import { describe, expect, it } from "vitest";
import { failedEvalSentence } from "../cellCommon";

describe("failedEvalSentence for the server's catch-all texts", () => {
  it("shows no sentence for the unexpected-error text", () => {
    expect(
      failedEvalSentence(
        "Evaluation failed. Please contact Future AGI support.",
      ),
    ).toBeNull();
    expect(
      failedEvalSentence(
        "Unable to process row. Please try again or contact Future AGI support.",
      ),
    ).toBeNull();
  });

  it("still keeps a specific reason that mentions support", () => {
    expect(
      failedEvalSentence("The judge model is not set; contact your admin"),
    ).toBe(
      "The evaluation failed: The judge model is not set; contact your admin",
    );
  });
});
