import { expect, it } from "vitest";
import { suggestRelease, suggestReviewer } from "./recoverySuggestion";

const release = (number, extra = {}) => ({ id: `v${number}`, number, eligible: true, selectable: true, isProduction: false, ...extra });
const failure = (versionId) => ({ latestFailure: { versionId } });

it("suggests the newest eligible release older than the failing one, skipping quarantined and newer releases", () => {
  const candidates = [
    release(4),
    release(3, { isProduction: true, eligible: false, selectable: true }),
    release(2, { eligible: false, selectable: false }),
    release(1),
  ];
  expect(suggestRelease({ candidates, cohort: [failure("v3"), {}] })).toEqual({ candidateId: "v1", number: 1, failing: 3 });
});

it("suggests nothing without a retained failure or without an eligible older release", () => {
  const candidates = [release(3, { isProduction: true }), release(2, { eligible: false, selectable: false })];
  expect(suggestRelease({ candidates, cohort: [{}] })).toBeNull();
  expect(suggestRelease({ candidates, cohort: [failure("v3")] })).toBeNull();
});

it("is conservative with several failing releases: older than the oldest failure", () => {
  const candidates = [release(5, { isProduction: true }), release(4), release(3), release(2)];
  expect(suggestRelease({ candidates, cohort: [failure("v4"), failure("v3")] })?.number).toBe(2);
});

it("suggests the person who last moved production, else the only reviewer, else nobody", () => {
  const reviewers = [{ id: "u2" }, { id: "u3" }];
  expect(suggestReviewer({ reviewers, lastReleaserId: "u3" })).toBe("u3");
  expect(suggestReviewer({ reviewers, lastReleaserId: "gone" })).toBe("");
  expect(suggestReviewer({ reviewers: [{ id: "u2" }], lastReleaserId: null })).toBe("u2");
  expect(suggestReviewer({ reviewers: undefined, lastReleaserId: "u3" })).toBe("");
});
