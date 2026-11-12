import React from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import AuthGuard from "src/auth/guard/auth-guard";

const state = vi.hoisted(() => ({
  authenticated: false,
  replace: vi.fn(),
  initialize: vi.fn(),
}));
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ replace: state.replace }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    loading: false,
    authenticated: state.authenticated,
    method: "jwt",
    initialize: state.initialize,
    user: state.authenticated
      ? {
          id: "self",
          email: "member@example.test",
          organization_role: "MEMBER",
          onboarding_completed: true,
        }
      : null,
  }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ isReady: true, currentOrganizationName: "Org A" }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  usePostLoginPath: () => "/dashboard/gateway",
}));
vi.mock("src/utils/googleAds", () => ({ trackSignupConversion: vi.fn() }));
vi.mock("src/utils/redditAds", () => ({ trackRedditSignup: vi.fn() }));
vi.mock("src/utils/twitterAds", () => ({ trackTwitterSignup: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  state.authenticated = false;
  window.history.replaceState(
    {},
    "",
    "/gateway/mcp/authorize?request=opaque-pending-request",
  );
});
it("preserves the complete gateway consent URL through login", async () => {
  render(
    <AuthGuard>
      <p>Consent form</p>
    </AuthGuard>,
  );
  await waitFor(() => expect(state.replace).toHaveBeenCalled());
  const target = new URL(state.replace.mock.calls[0][0], "http://localhost");
  expect(target.pathname).toBe("/auth/jwt/login");
  expect(target.searchParams.get("returnTo")).toBe(
    "/gateway/mcp/authorize?request=opaque-pending-request",
  );
  expect(screen.queryByText("Consent form")).not.toBeInTheDocument();
});
it("shows consent on the first signed-in render rather than redirecting to onboarding", async () => {
  state.authenticated = true;
  render(
    <AuthGuard>
      <p>Consent form</p>
    </AuthGuard>,
  );
  await screen.findByText("Consent form");
  expect(state.replace).not.toHaveBeenCalled();
});
