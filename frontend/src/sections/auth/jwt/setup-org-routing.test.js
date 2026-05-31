import { describe, expect, it } from "vitest";

import { paths } from "src/routes/paths";

import {
  isSetupCompletionHandoff,
  persistSetupCompletionReturnTo,
  resolveSetupCompletionHref,
  shouldShowInviteStepAfterProfileSave,
  setupCompletionHomeHref,
} from "./setup-org-routing";

describe("setup org completion routing", () => {
  it("defaults new users to the first-run home", () => {
    expect(setupCompletionHomeHref()).toBe(
      `${paths.dashboard.home}?source=setup_org`,
    );
    expect(resolveSetupCompletionHref(null)).toBe(setupCompletionHomeHref());
  });

  it("carries product-loop quick-start attribution to onboarding home", () => {
    expect(
      resolveSetupCompletionHref({
        goal: "monitor_production_ai_app",
        id: "observe",
        primaryPath: "observe",
      }),
    ).toBe(
      `${paths.dashboard.home}?source=setup_org&quick_start_id=observe&quick_start_goal=monitor_production_ai_app&quick_start_primary_path=observe`,
    );
  });

  it("persists a safe one-shot return target for auth guard handoff", () => {
    localStorage.clear();

    expect(
      persistSetupCompletionReturnTo(
        `${paths.dashboard.home}?source=setup_org&quick_start_id=observe`,
      ),
    ).toBe(true);
    expect(localStorage.getItem("redirectUrl")).toBe(
      `${paths.dashboard.home}?source=setup_org&quick_start_id=observe`,
    );
    expect(
      isSetupCompletionHandoff(
        `${paths.dashboard.home}?source=setup_org&quick_start_id=observe`,
      ),
    ).toBe(true);
  });

  it("rejects unsafe setup completion return targets", () => {
    localStorage.clear();
    sessionStorage.clear();

    expect(persistSetupCompletionReturnTo("https://example.com")).toBe(false);
    expect(persistSetupCompletionReturnTo("//example.com/dashboard")).toBe(
      false,
    );
    expect(localStorage.getItem("redirectUrl")).toBeNull();
  });

  it("does not treat unrelated dashboard paths as setup handoffs", () => {
    localStorage.clear();
    sessionStorage.clear();
    persistSetupCompletionReturnTo(
      `${paths.dashboard.home}?source=setup_org&quick_start_id=observe&quick_start_goal=monitor_production_ai_app&quick_start_primary_path=observe`,
    );

    expect(isSetupCompletionHandoff(paths.dashboard.home)).toBe(false);
    expect(
      isSetupCompletionHandoff(
        `${paths.dashboard.home}?source=setup_org&quick_start_id=observe`,
      ),
    ).toBe(true);
    expect(
      isSetupCompletionHandoff(
        `${paths.dashboard.home}?quick_start_id=observe&source=setup_org`,
      ),
    ).toBe(true);
    expect(
      isSetupCompletionHandoff(
        `${paths.dashboard.home}?sample=true&quick_start_goal=monitor_production_ai_app&quick_start_id=observe&quick_start_primary_path=observe`,
      ),
    ).toBe(true);
    expect(
      isSetupCompletionHandoff(
        `${paths.dashboard.home}?source=setup_org&quick_start_id=observe-2`,
      ),
    ).toBe(false);
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

  it("keeps owner invites off the product-loop quick-start path", () => {
    expect(
      shouldShowInviteStepAfterProfileSave({
        isOwner: true,
        quickStartRequested: true,
      }),
    ).toBe(false);
    expect(
      shouldShowInviteStepAfterProfileSave({
        isOwner: true,
        quickStartRequested: false,
      }),
    ).toBe(true);
    expect(
      shouldShowInviteStepAfterProfileSave({
        isOwner: false,
        quickStartRequested: false,
      }),
    ).toBe(false);
  });
});
