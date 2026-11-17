import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, renderHook } from "@testing-library/react";
import { MemoryRouter, matchRoutes } from "react-router-dom";
import { dashboardRoutes } from "../dashboard";
import { useFeatureRouteAnswer } from "src/routes/hooks/use-feature-route-answer";
import Router from "..";

// The release Settings contract on the composed router: denied URLs are absent;
// pending/error URLs stay registered so the capability gate can load or retry.
const FALCON = "/dashboard/settings/falcon-ai-connectors";
const h = vi.hoisted(() => ({
  user: { organization_role: "Owner", ws_enabled: false },
  capability: { allowed: false, isLoading: false, isError: false },
  options: [],
}));
vi.mock("../dashboard", async (original) => {
  const actual = /** @type {typeof import("../dashboard")} */ (
    await original()
  );
  return { ...actual, dashboardRoutes: vi.fn(actual.dashboardRoutes) };
});
vi.mock("../main", () => ({ mainRoutes: [] }));
vi.mock("../auth", () => ({ authRoutes: [] }));
vi.mock("src/pages/SOSLoginPage", () => ({ default: () => null }));
vi.mock("src/components/loading-screen", () => ({
  SplashScreen: () => null,
  LoadingScreen: () => null,
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ user: h.user }) }));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: false,
    isSuccess: true,
    isLoading: false,
    isCloud: true,
  }),
  usePostLoginPath: () => "/dashboard",
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureAllowed: (_feature, options) => {
    h.options.push(options);
    return h.capability;
  },
}));
beforeEach(() => {
  h.user = { organization_role: "Owner", ws_enabled: false };
  h.capability = { allowed: false, isLoading: false, isError: false };
  h.options = [];
  vi.mocked(dashboardRoutes).mockClear();
});
describe.each([true, false])("Settings routes, isCloud=%s", (isCloud) => {
  const routes = (falconAI = "denied") =>
    dashboardRoutes(null, null, { isCloud, falconAI });
  it("does not route Coming Soon", () => {
    expect(matchRoutes(routes(), "/dashboard/settings/coming-soon")).toBeNull();
  });
  it("does not route a denied Falcon capability", () => {
    expect(matchRoutes(routes(), FALCON)).toBeNull();
  });
  it.each(["allowed", "pending"])("routes Falcon when %s", (answer) => {
    expect(matchRoutes(routes(answer), FALCON)?.at(-1)?.route.path).toBe(
      "falcon-ai-connectors",
    );
  });
  it("still routes Profile settings", () => {
    expect(
      matchRoutes(routes(), "/dashboard/settings/profile-settings")?.at(-1)
        ?.route.path,
    ).toBe("profile-settings");
  });
});
describe("the shared capability route answer", () => {
  it.each([
    [{ allowed: true }, true, "allowed"],
    [{ allowed: false }, true, "denied"],
    [{ allowed: false, isLoading: true }, true, "pending"],
    [{ allowed: false, isError: true }, true, "pending"],
    [{ allowed: false }, false, "pending"],
  ])(
    "preserves capability state %j with enabled=%s",
    (state, enabled, answer) => {
      h.capability = { ...h.capability, ...state };
      expect(
        renderHook(() => useFeatureRouteAnswer("falcon_ai", { enabled })).result
          .current,
      ).toBe(answer);
      expect(h.options.at(-1)).toEqual({ enabled });
    },
  );
});
describe("the app router", () => {
  const show = () =>
    render(
      <MemoryRouter initialEntries={["/sos"]}>
        <Router />
      </MemoryRouter>,
    );
  const answer = () =>
    vi.mocked(dashboardRoutes).mock.calls.at(-1)?.[2]?.falconAI;
  it.each([true, false])("routes the actual allowed=%s answer", (allowed) => {
    h.capability.allowed = allowed;
    show();
    expect(answer()).toBe(allowed ? "allowed" : "denied");
  });
  it("does not read signed-out capabilities and preserves the URL", () => {
    h.user = null;
    show();
    expect(h.options.at(-1)).toEqual({ enabled: false });
    expect(answer()).toBe("pending");
  });
});
