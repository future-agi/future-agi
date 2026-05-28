import { describe, expect, it } from "vitest";

import { paths } from "src/routes/paths";

import {
  resolveSetupCompletionHref,
  setupCompletionHomeHref,
} from "./setup-org-routing";

describe("setup org completion routing", () => {
  it("defaults new users to the first-run home", () => {
    expect(setupCompletionHomeHref()).toBe(
      `${paths.dashboard.home}?source=setup_org`,
    );
    expect(resolveSetupCompletionHref(null)).toBe(setupCompletionHomeHref());
  });

  it("ignores internal return targets after setup so activation can resolve", () => {
    expect(resolveSetupCompletionHref("/dashboard/observe?project=1")).toBe(
      setupCompletionHomeHref(),
    );
  });

  it("ignores external, protocol-relative, and auth return targets", () => {
    expect(resolveSetupCompletionHref("https://example.com/dashboard")).toBe(
      setupCompletionHomeHref(),
    );
    expect(resolveSetupCompletionHref("//example.com/dashboard")).toBe(
      setupCompletionHomeHref(),
    );
    expect(resolveSetupCompletionHref("/auth/jwt/login")).toBe(
      setupCompletionHomeHref(),
    );
  });
});
