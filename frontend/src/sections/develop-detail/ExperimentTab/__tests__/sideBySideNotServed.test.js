// The Experiments tab's side-by-side door (the hover button, the row menu and
// the right-click menu share it) where the server has no side-by-side read:
// the door keeps its place, turned off with the shared line, and opens no
// page. Where the read is served, it opens the page as before.
import { afterEach, describe, expect, it } from "vitest";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import { sideBySideDisabledReason, sideBySideHref } from "../sideBySide";

const run = (label, exp) => ({ run_id: label, label, experiment_id: exp });
const A1 = run("#1A", "e1");
const B1 = run("#1B", "e1");
const B2 = run("#2B", "e2");
const ctx = {
  datasetId: "d",
  baselineId: A1.run_id,
  baselineRun: A1,
  pinnedRunId: A1.run_id,
  allRuns: [A1, B1, B2],
};

afterEach(() => setRealBackendForTests(null));

describe("side-by-side door where the server has no side-by-side read", () => {
  it("opens no page", () => {
    setRealBackendForTests(true);
    expect(sideBySideHref(B2, ctx)).toBeNull();
    expect(sideBySideHref(B2, ctx, B1)).toBeNull();
  });

  it("says the shared line as its reason", () => {
    setRealBackendForTests(true);
    expect(sideBySideDisabledReason(B2, ctx)).toBe(NOT_IN_RELEASE);
  });
});

describe("side-by-side door where the read is served", () => {
  it("opens the page as before", () => {
    setRealBackendForTests(false);
    const href = sideBySideHref(B2, ctx);
    expect(href).toContain("/dashboard/develop/d/compare/tasks");
    expect(href).toContain("runs=%232B%2C%231A");
    expect(sideBySideDisabledReason(B2, ctx)).toBeNull();
  });
});
