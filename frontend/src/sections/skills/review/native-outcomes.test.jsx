import { MemoryRouter } from "react-router-dom";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ReleaseReviewBody from "./ReleaseReviewBody";
import { capability, changes, nativeEvidence, packet } from "../__tests__/r3_l09/fixtures";
import { nativeCaseLines, nativeConclusion, nativeSummaryConclusion } from "../utils/conclusions";

afterEach(cleanup);

const cell = (variant, version, verdicts = {}, status = "completed") => ({
  caseId: "case-1", title: "Blender refund refunded shipping", variant, version, repeatIndex: 0, status,
  answer: "pass", selection: "pass", delivery: "pass", tools: "pass", ...verdicts,
});
const repair = {
  source: "simulation_native", sourceId: nativeEvidence.sourceId, planned: 2, graded: 2, incomplete: 0, regressions: 0,
  answer: "pass", selection: "pass", delivery: "pass", tools: "pass", disagreements: 0, availability: "available",
  result: { state: "works_on_examples", reproduction: "reproduced", repair_verified: true },
  cases: [cell("candidate", 7), cell("reference", 4, { tools: "fail" })],
};

describe("application verification headline", () => {
  it("speaks about a repair only when the run carried a recorded failure", () => {
    expect(nativeConclusion({ candidate: 2, passed: 3, total: 3, result: { reproduction: "not_requested", repair_verified: false } })).toBe("v2 passed 3 of 3 cases.");
    expect(nativeConclusion({ candidate: 2, reference: 1, passed: 1, total: 1, result: { reproduction: "reproduced", repair_verified: true } })).toBe("Repair verified: v2 passed 1 of 1 cases; the recorded failure reproduced on v1.");
    expect(nativeConclusion({ candidate: 2, passed: 0, total: 1, result: { reproduction: "not_reproduced", repair_verified: false } })).toBe("Repair not verified: v2 passed 0 of 1 cases; the recorded failure did not reproduce.");
    expect(nativeConclusion({ decision: "hold" })).toBe("Hold this release.");
  });
  it("names failed checks per version and never calls ungraded work a pass", () => {
    expect(nativeCaseLines(repair.cases)).toEqual([{ caseId: "case-1", title: "Blender refund refunded shipping", text: "v7 passed every check · v4 failed: Business tool" }]);
    expect(nativeCaseLines([cell("candidate", 7, { answer: "unverifiable" })])[0].text).toBe("v7 not fully graded");
    expect(nativeCaseLines([cell("candidate", 7, {}, "error")])[0].text).toBe("v7 not fully graded");
    expect(nativeSummaryConclusion({ ...repair, cases: [cell("candidate", 7, { answer: "not_graded" }), repair.cases[1]], result: { ...repair.result, repair_verified: false } }))
      .toBe("Repair not verified: v7 passed 0 of 1 cases; the recorded failure reproduced on v4.");
    expect(nativeSummaryConclusion({ ...repair, result: null })).toBeNull();
  });
});

describe("review packet evidence", () => {
  it("shows the attached run's own outcomes before the reviewer decides", () => {
    render(<MemoryRouter><ReleaseReviewBody packet={packet} changes={changes} capabilities={capability} summaries={[repair]} /></MemoryRouter>);
    const headline = screen.getByText("Repair verified: v7 passed 1 of 1 cases; the recorded failure reproduced on v4.");
    expect(headline).toBeVisible();
    expect(screen.getByText("Blender refund refunded shipping: v7 passed every check · v4 failed: Business tool")).toBeVisible();
    // The row states the outcome once; exact grading counts and the evidence ID are read in Inspect.
    expect(screen.queryByText(/attempts graded/)).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Inspect", exact: true })[1]);
    expect(screen.getByText(/2\/2 attempts graded · 0 incomplete · 0 regressions/)).toBeVisible();
    expect(screen.getByText("Evidence ID")).toBeVisible();
    // Only the text comparison (no summary supplied here) keeps the unknown-result notice.
    expect(within(headline.closest("tr")).queryByText(/Result not recorded/)).not.toBeInTheDocument();
    expect(within(headline.closest("tr")).getAllByText("Passed")).toHaveLength(4);
  });
  it("keeps an unavailable native result unknown", () => {
    render(<MemoryRouter><ReleaseReviewBody packet={packet} changes={changes} capabilities={capability} summaries={[{ ...repair, result: null, cases: [], graded: null, availability: "unavailable", answer: "Not observed", selection: "Not observed", delivery: "Not observed", tools: "Not observed" }]} /></MemoryRouter>);
    expect(screen.queryByText(/Repair verified/)).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Inspect", exact: true })[1]);
    expect(screen.getByText(/Unknown\/2 attempts graded/)).toBeVisible();
  });
});
