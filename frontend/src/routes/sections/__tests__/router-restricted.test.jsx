import { render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Outlet, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Router from "../index";

// The real Router with the real dashboardRoutes, fed the user the app loads
// at boot. Only the guards and the dashboard layout are stubbed, so the test
// shows which URL the route tree ends on.
const authState = { user: null };

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));
vi.mock("src/auth/guard", async (importOriginal) => {
  const actual = await importOriginal();
  const pass = ({ children }) => children ?? <Outlet />;
  return Object.fromEntries(Object.keys(actual).map((name) => [name, pass]));
});
vi.mock("src/layouts/dashboard", () => ({
  default: ({ children }) => children ?? <Outlet />,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: false,
    isCloud: true,
    isSuccess: true,
    isLoading: false,
  }),
  usePostLoginPath: () => "/dashboard/develop",
}));

function CurrentUrl() {
  return <p data-testid="url">{useLocation().pathname}</p>;
}

function renderAt(url) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[url]}>
        <Router />
        <CurrentUrl />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

const HIDDEN = "/dashboard/simulate/scenarios";

describe("Router with the user-info restriction flag", () => {
  beforeEach(() => {
    authState.user = { organization_role: "Owner" };
  });

  it("sends an org restricted to Environments to the 404 for a hidden page", async () => {
    authState.user.restrict_simulate_to_environments = true;
    renderAt(HIDDEN);
    expect(await screen.findByText("/404", {}, { timeout: 5000 })).toBeTruthy();
  });

  it("keeps the page for an org without the flag", async () => {
    authState.user.restrict_simulate_to_environments = false;
    renderAt(HIDDEN);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByTestId("url")).toHaveTextContent(HIDDEN);
  });
});
