// L7c g6 (status:ledger): the footer keeps the errored-tries count as plain
// text when the re-score door is hidden.
import { describe, expect, it } from "vitest";
import { erroredTriesOf, erroredTriesText } from "../erroredTries";

describe("errored tries in the footer (L7c g6)", () => {
  it("counts every run in view, one count per policy, runs in the title", () => {
    const c = erroredTriesOf([
      { label: "#2A", errored_tries: 1, error_policy: "zero" },
      { label: "#2E", errored_tries: 2 },
      { label: "#2G", errored_tries: 0 },
    ]);
    expect(c).toMatchObject({ zero: 3, leftOut: 0 });
    expect(c.title).toBe(
      "Errored tries count as 0 (a fail) in every score: #2A 1 · #2E 2.",
    );
    expect(erroredTriesText(c.zero, c.leftOut)).toBe(
      "3 errored tries counted as 0",
    );
  });
  it("a mixed view prints one clause per policy", () => {
    const c = erroredTriesOf([
      { label: "#2A", errored_tries: 1, error_policy: "exclude" },
      { label: "#2E", errored_tries: 2, error_policy: "zero" },
    ]);
    expect(erroredTriesText(c.zero, c.leftOut)).toBe(
      "2 errored tries counted as 0 · 1 errored try left out of scores",
    );
  });
  it("no errored tries, no text", () => {
    const c = erroredTriesOf([{ label: "#2G" }]);
    expect(c).toEqual({ zero: 0, leftOut: 0, title: null });
    expect(erroredTriesText(0, 0)).toBe("");
  });
});
