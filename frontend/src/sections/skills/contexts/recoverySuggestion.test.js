import { describe, expect, it } from "vitest";
import { canMoveFromTracker, checkedFiles, resumedRollback, rollbackAcknowledgment, rollbackProgress, suggestRelease, suggestReviewer, trackerSteps } from "./recoverySuggestion";

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

// ---- One-page rollback (UXF-05, UXF-08) ----

const DESCRIPTOR = ".futureagi/context.json";

it("counts the changed files a person checks, never the generated settings file or unchanged files", () => {
  const rows = [
    { path: DESCRIPTOR, status: "changed" },
    { path: "AGENTS.md", status: "changed" },
    { path: "skills/a/SKILL.md", status: "added" },
    { path: "skills/b/SKILL.md", status: "unchanged" },
  ];
  expect(checkedFiles(rows, DESCRIPTOR).map((row) => row.path)).toEqual(["AGENTS.md", "skills/a/SKILL.md"]);
  expect(checkedFiles(undefined, DESCRIPTOR)).toEqual([]);
});

it("words the one tick by what is open above it", () => {
  expect(rollbackAcknowledgment({ files: 2 })).toBe("I've checked the 2 changed files");
  expect(rollbackAcknowledgment({ files: 1 })).toBe("I've checked the 1 changed file");
  expect(rollbackAcknowledgment({ files: 2, linkedSkillChanges: true })).toBe("I've checked the 2 changed files and the linked skill versions");
  expect(rollbackAcknowledgment({ files: 0 })).toBe("I've checked the changes");
  expect(rollbackAcknowledgment()).toBe("I've checked the changes");
});

it("names the tracker steps as the spec words them, and reads finished steps as what happened", () => {
  expect(trackerSteps("Priya Shah")).toEqual(["Waiting for Priya Shah", "Approved: move production", "Check apps updated"]);
  expect(trackerSteps("Priya Shah", 1)).toEqual(["Approved by Priya Shah", "Approved: move production", "Check apps updated"]);
  expect(trackerSteps(undefined, 2)).toEqual(["Approved by your reviewer", "Production moved", "Check apps updated"]);
});

it("maps the server's request status to a tracker step, and unknown or loading to waiting, never approved", () => {
  expect(rollbackProgress({ awaiting: false })).toEqual({ step: -1, phase: "form" });
  expect(rollbackProgress({ awaiting: true })).toEqual({ step: 0, phase: "waiting" });
  expect(rollbackProgress({ awaiting: true, status: "pending" })).toEqual({ step: 0, phase: "waiting" });
  expect(rollbackProgress({ awaiting: true, status: "something_new" })).toEqual({ step: 0, phase: "waiting" });
  expect(rollbackProgress({ awaiting: true, status: "approved" })).toEqual({ step: 1, phase: "approved" });
  expect(rollbackProgress({ awaiting: true, status: "changes_requested" })).toEqual({ step: 0, phase: "closed" });
  expect(rollbackProgress({ awaiting: true, status: "superseded" })).toEqual({ step: 0, phase: "closed" });
  expect(rollbackProgress({ awaiting: true, status: "released" })).toEqual({ step: 2, phase: "moved" });
  expect(rollbackProgress({ awaiting: false, isProduction: true })).toEqual({ step: 2, phase: "moved" });
  // The status of an earlier request never opens the app check for a version that is not in production.
  expect(rollbackProgress({ awaiting: false, status: "released" })).toEqual({ step: -1, phase: "form" });
  expect(rollbackProgress({ awaiting: false, status: "approved" })).toEqual({ step: -1, phase: "form" });
});

describe("canMoveFromTracker", () => {
  const submitted = { requestId: "req-1", fingerprint: "fp", requestRevision: 0 };
  const read = {
    request: { id: "req-1", status: "approved", revision: 0 },
    preview: { current: true, fingerprint: "fp" },
    canExecute: true,
  };

  it("allows the move only when the server says approved, current and executable for exactly this request", () => {
    expect(canMoveFromTracker({ read, submitted })).toBe(true);
  });

  it("refuses without a read, a submission or a fingerprint", () => {
    expect(canMoveFromTracker({ read: undefined, submitted })).toBe(false);
    expect(canMoveFromTracker({ read, submitted: undefined })).toBe(false);
    expect(canMoveFromTracker({ read, submitted: { ...submitted, fingerprint: undefined } })).toBe(false);
    expect(canMoveFromTracker({})).toBe(false);
  });

  it.each([
    ["not approved yet", { request: { ...read.request, status: "pending" } }],
    ["another request", { request: { ...read.request, id: "req-2" } }],
    ["no authority to execute", { canExecute: false }],
    ["authority unknown", { canExecute: undefined }],
    ["packet no longer current", { preview: { current: false, fingerprint: "fp" } }],
    ["stale code", { staleCode: "release_conflict" }],
    ["renewal required", { renewalRequired: "evidence_expired" }],
    ["packet fingerprint changed after the tick", { preview: { current: true, fingerprint: "other" } }],
    ["renewed to another revision", { request: { ...read.request, revision: 1 } }],
  ])("refuses when %s", (_name, change) => {
    expect(canMoveFromTracker({ read: { ...read, ...change }, submitted })).toBe(false);
  });

  it("still binds the fingerprint when the server reported no request revision", () => {
    const noRevision = { requestId: "req-1", fingerprint: "fp" };
    expect(canMoveFromTracker({ read, submitted: noRevision })).toBe(true);
    expect(canMoveFromTracker({ read: { ...read, preview: { current: true, fingerprint: "x" } }, submitted: noRevision })).toBe(false);
  });
});

describe("resumedRollback (UXF-08)", () => {
  const candidates = [
    { id: "v3", number: 3, isProduction: true },
    { id: "v2", number: 2, isProduction: false },
    { id: "v4", number: 4, isProduction: false },
  ];
  const request = (patch = {}) => ({ requestId: "req-1", requestRevision: 1, status: "pending", candidateVersionId: "v2", label: "production", expectedReleaseRevision: 7, reviewer: "Priya Shah", ...patch });
  it("resumes this person's production rollback made against the current release", () => {
    expect(resumedRollback({ requests: [request()], candidates, releaseRevision: 7 })).toEqual({
      candidateId: "v2", from: 3, revision: 7, reviewer: "Priya Shah", requestId: "req-1", requestRevision: 1, resumed: true,
    });
  });
  it("never carries a packet fingerprint, so the tracker cannot move production itself", () => {
    const resumed = resumedRollback({ requests: [request({ status: "approved" })], candidates, releaseRevision: 7 });
    expect(resumed).not.toHaveProperty("fingerprint");
    const read = { request: { id: "req-1", status: "approved", revision: 1 }, preview: { current: true, fingerprint: "fp" }, canExecute: true };
    expect(canMoveFromTracker({ read, submitted: resumed })).toBe(false);
  });
  it.each([
    ["a forward release", request({ candidateVersionId: "v4" })],
    ["another label", request({ label: "staging" })],
    ["a request made before production last moved", request({ expectedReleaseRevision: 6 })],
    ["an unknown release", request({ candidateVersionId: "gone" })],
  ])("does not resume %s", (_name, value) => {
    expect(resumedRollback({ requests: [value], candidates, releaseRevision: 7 })).toBeNull();
  });
  it("needs a production release and a release revision", () => {
    expect(resumedRollback({ requests: [request()], candidates: candidates.map((row) => ({ ...row, isProduction: false })), releaseRevision: 7 })).toBeNull();
    expect(resumedRollback({ requests: [request()], candidates, releaseRevision: undefined })).toBeNull();
  });
});
