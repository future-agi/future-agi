import { describe, expect, it } from "vitest";

import { paths } from "src/routes/paths";

import { normalizeActivationState } from "../activation-state-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";
import {
  isSafePostLoginReturnTo,
  resolvePostLoginDestination,
  routeForAnalytics,
} from "../utils/post-login-routing";

const flagsOn = {
  onboarding_activation_state_api: true,
  onboarding_first_run_home: true,
  onboarding_release_0_internal: true,
  onboarding_daily_quality_home: false,
};

const flagsOff = {
  ...flagsOn,
  onboarding_first_run_home: false,
};

const baseUser = {
  id: "user-1",
  onboarding_completed: true,
  organization_role: "Admin",
  default_workspace_role: "workspace_admin",
};

const viewerUser = {
  ...baseUser,
  default_workspace_role: "workspace_viewer",
};

const state = (name) =>
  normalizeActivationState(getActivationStateFixture(name));

const resolve = (overrides = {}) =>
  resolvePostLoginDestination({
    currentPath: paths.dashboard.falconAI,
    user: baseUser,
    deploymentMode: "cloud",
    fallbackDestination: paths.dashboard.falconAI,
    flags: flagsOn,
    activationState: state("observeNoSetup"),
    ...overrides,
  });

describe("post-login routing", () => {
  it("accepts only relative non-auth return targets", () => {
    expect(isSafePostLoginReturnTo("/dashboard/observe?trace=1")).toBe(true);
    expect(isSafePostLoginReturnTo("https://example.com/dashboard")).toBe(
      false,
    );
    expect(isSafePostLoginReturnTo("//example.com/dashboard")).toBe(false);
    expect(isSafePostLoginReturnTo("/auth/jwt/login")).toBe(false);
  });

  it("preserves a safe return target before rollout decisions", () => {
    const destination = resolve({
      returnTo: "/dashboard/observe?project=1",
      flags: flagsOff,
    });

    expect(destination.href).toBe("/dashboard/observe?project=1");
    expect(destination.reason).toBe("safe_return_to");
    expect(destination.usedReturnTo).toBe(true);
    expect(destination.shouldClearReturnTo).toBe(true);
  });

  it("rejects unsafe return targets and clears them after the decision", () => {
    const destination = resolve({
      returnTo: "https://example.com/dashboard",
    });

    expect(destination.href).toBe(paths.dashboard.home);
    expect(destination.reason).toBe("internal_onboarding_home");
    expect(destination.usedReturnTo).toBe(false);
    expect(destination.shouldClearReturnTo).toBe(true);
  });

  it("preserves direct dashboard routes", () => {
    const destination = resolve({
      currentPath: "/dashboard/observe?project=1",
      activationState: null,
    });

    expect(destination.href).toBe("/dashboard/observe?project=1");
    expect(destination.reason).toBe("direct_dashboard_route");
    expect(destination.shouldReplace).toBe(false);
  });

  it("does not preserve the old fallback when the home rollout is eligible", () => {
    const destination = resolve({
      currentPath: paths.dashboard.falconAI,
    });

    expect(destination.href).toBe(paths.dashboard.home);
    expect(destination.reason).toBe("internal_onboarding_home");
    expect(destination.shouldReplace).toBe(true);
  });

  it("uses the deployment fallback when a required flag is off", () => {
    const destination = resolve({
      flags: flagsOff,
    });

    expect(destination.href).toBe(paths.dashboard.falconAI);
    expect(destination.reason).toBe("required_flag_off");
  });

  it("preserves OSS fallback when flags are off", () => {
    const destination = resolve({
      currentPath: paths.dashboard.develop,
      deploymentMode: "oss",
      fallbackDestination: paths.dashboard.develop,
      flags: flagsOff,
    });

    expect(destination.href).toBe(paths.dashboard.develop);
    expect(destination.reason).toBe("required_flag_off");
  });

  it("uses the deployment fallback when activation state fails", () => {
    const destination = resolve({
      activationState: null,
      activationStateError: new Error("offline"),
    });

    expect(destination.href).toBe(paths.dashboard.falconAI);
    expect(destination.reason).toBe("activation_state_error");
  });

  it("routes eligible first-run stages to the onboarding home", () => {
    expect(resolve({ activationState: state("newWorkspaceNoGoal") }).href).toBe(
      paths.dashboard.home,
    );
    expect(
      resolve({ activationState: state("observeWaitingWithSample") }).href,
    ).toBe(paths.dashboard.home);
    expect(
      resolve({ activationState: state("observeFirstTraceReady") }).href,
    ).toBe(paths.dashboard.home);
  });

  it("routes activated users to daily quality when that flag is enabled", () => {
    const destination = resolve({
      flags: {
        ...flagsOn,
        onboarding_daily_quality_home: true,
      },
      activationState: state("observeFirstLoopComplete"),
    });

    expect(destination.href).toBe(`${paths.dashboard.home}?mode=daily-quality`);
    expect(destination.reason).toBe("daily_quality_home");
  });

  it("routes activated users to home when the daily quality flag is off", () => {
    const destination = resolve({
      activationState: state("observeFirstLoopComplete"),
    });

    expect(destination.href).toBe(paths.dashboard.home);
    expect(destination.reason).toBe("internal_onboarding_home");
  });

  it("uses the activation-state fallback when the state is feature disabled", () => {
    const destination = resolve({
      activationState: state("featureDisabled"),
    });

    expect(destination.href).toBe(paths.dashboard.getstarted);
    expect(destination.reason).toBe("activation_feature_disabled");
  });

  it("keeps org setup and incomplete onboarding branches unchanged", () => {
    expect(
      resolve({
        user: {
          ...baseUser,
          requires_org_setup: true,
        },
      }).href,
    ).toBe(paths.auth.jwt.org_removed);

    const incomplete = resolve({
      currentPath: "/auth/jwt/setup-org",
      user: {
        ...baseUser,
        onboarding_completed: false,
      },
    });
    expect(incomplete.href).toBe("/auth/jwt/setup-org");
    expect(incomplete.shouldReplace).toBe(false);
  });

  it("keeps viewer fallback unless activation state supports permission-limited home", () => {
    expect(resolve({ user: viewerUser }).href).toBe(paths.dashboard.falconAI);

    const permissionLimited = resolve({
      user: viewerUser,
      activationState: state("permissionLimitedViewer"),
    });
    expect(permissionLimited.href).toBe(paths.dashboard.home);
    expect(permissionLimited.reason).toBe("internal_onboarding_home");
  });

  it("does not create a redirect loop when already on home", () => {
    const destination = resolve({
      currentPath: paths.dashboard.home,
    });

    expect(destination.href).toBe(paths.dashboard.home);
    expect(destination.shouldReplace).toBe(false);
  });

  it("removes query strings from analytics route values", () => {
    expect(routeForAnalytics("/dashboard/home?mode=daily-quality")).toBe(
      paths.dashboard.home,
    );
  });
});
