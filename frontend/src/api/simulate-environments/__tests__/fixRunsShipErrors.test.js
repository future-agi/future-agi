import { describe, it, expect } from "vitest";

import { fixErrorOf } from "../fixRuns";

// W10-AGENT-FIX-U05: git or GitHub did not take the change, so the patch is a
// way out. G3-UX-1: whether Ship stays beside it is the server's retryable
// flag, which fixErrorOf passes on.
describe("fixErrorOf on a GitHub ship that failed", () => {
  it.each([
    "fix_github_patch_failed",
    "fix_github_branch_changed",
    "fix_github_push_failed",
    "fix_github_pull_request_failed",
  ])(
    "offers the patch when git or GitHub did not take the change: %s",
    (code) => {
      expect(
        fixErrorOf({ statusCode: 409, error: code, message: "m" }).offerPatch,
      ).toBe(true);
    },
  );

  it("keeps Ship, with no patch, when the version could not become the default", () => {
    expect(
      fixErrorOf({
        statusCode: 409,
        error: "fix_candidate_not_activated",
        message: "m",
      }).offerPatch,
    ).toBe(false);
  });

  it.each([true, false])(
    "passes on the server's retryable flag (%s) from the app's error shape and axios's",
    (retryable) => {
      const body = {
        error: "fix_github_pull_request_failed",
        message: "m",
        retryable,
      };
      expect(fixErrorOf({ statusCode: 502, ...body }).retryable).toBe(
        retryable,
      );
      expect(
        fixErrorOf({ response: { status: 502, data: body } }).retryable,
      ).toBe(retryable);
    },
  );
});
