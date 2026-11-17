/* eslint-disable react/prop-types */
import React, { useCallback, useContext, useMemo, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { act, fireEvent, render, screen } from "src/utils/test-utils";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import GuestGuard from "src/auth/guard/guest-guard";
import OrgRemovedPage from "../org-removed";

// W10-AUTH-ORG-U03: a signed-in user with no organization is sent to
// /auth/jwt/org-removed from every route (AuthGuard), and GuestGuard sends a
// signed-in visit to the sign-in page back to the dashboard. The page must let
// them sign out (wrong account, shared device) instead of forcing them to
// create an organization.

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    auth: { createOrganization: "/accounts/organizations/create/" },
  },
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));
vi.mock("src/utils/logger", () => ({ default: { error: vi.fn() } }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));

const EMAIL = "removed@example.com";
const ORG_REMOVED = "/auth/jwt/org-removed";
const SIGN_IN = "/auth/jwt/login";

function FakeAuth({ children, user, onLogout }) {
  const [authenticated, setAuthenticated] = useState(true);
  const logout = useCallback(() => {
    // As auth-provider.jsx: the session is cleared before the first await.
    setAuthenticated(false);
    onLogout();
    return new Promise((resolve) => setTimeout(resolve, 20));
  }, [onLogout]);
  const value = useMemo(
    () => ({
      authenticated,
      loading: false,
      method: "jwt",
      user: authenticated ? user : null,
      initialize: () => Promise.resolve(),
      logout,
    }),
    [authenticated, logout, user],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// As AuthGuard (auth-guard.jsx): a signed-in user with no organization is
// sent back to the org-removed page from every guarded route.
function GuardedDashboard() {
  const { authenticated, user } = useContext(AuthContext);
  if (authenticated && user?.requires_org_setup) {
    return <Navigate to={ORG_REMOVED} replace />;
  }
  return <h1>Dashboard</h1>;
}

function Track({ seen }) {
  const location = useLocation();
  seen.push(location.pathname);
  return null;
}

function renderOrgRemoved({ user, onLogout = () => undefined, seen = [] }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FakeAuth user={user} onLogout={onLogout}>
        <Track seen={seen} />
        <Routes>
          <Route path={ORG_REMOVED} element={<OrgRemovedPage />} />
          <Route
            path={SIGN_IN}
            element={
              <GuestGuard>
                <h1>Sign in</h1>
              </GuestGuard>
            }
          />
          <Route path="/dashboard/*" element={<GuardedDashboard />} />
        </Routes>
      </FakeAuth>
    </QueryClientProvider>,
  );
}

describe("org-removed page sign-out (W10-AUTH-ORG-U03)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", ORG_REMOVED);
  });

  it("shows who is signed in and a Sign out that lands on the sign-in page and stays there", async () => {
    const onLogout = vi.fn();
    const seen = [];
    renderOrgRemoved({
      user: { email: EMAIL, requires_org_setup: true },
      onLogout,
      seen,
    });

    expect(screen.getByText("any organization")).toBeInTheDocument();
    expect(
      screen.getByText(/Signed in as removed@example\.com\. Not you\?/),
    ).toBeInTheDocument();
    const signOut = screen.getByRole("button", { name: "Sign out" });
    expect(signOut).toBeEnabled();

    fireEvent.click(signOut);
    await act(() => new Promise((resolve) => setTimeout(resolve, 60)));

    expect(onLogout).toHaveBeenCalledTimes(1);
    expect(window.location.pathname).toBe(SIGN_IN);
    expect(window.location.search).toBe("");
    expect(
      screen.getByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("any organization")).not.toBeInTheDocument();
    // GuestGuard never bounced a still-signed-in session to the dashboard,
    // which would have sent it straight back to the org-removed page.
    expect(seen).not.toContain("/dashboard");
    expect(seen.at(-1)).toBe(SIGN_IN);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("tells the user how to rejoin through an invitation", () => {
    renderOrgRemoved({ user: { email: EMAIL, requires_org_setup: true } });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Your account is still active. Create a new organization, or ask your admin to re-invite you and open the invitation email to rejoin.",
    );
  });

  it("still offers Sign out when the account email is not known", () => {
    renderOrgRemoved({ user: { requires_org_setup: true } });

    expect(screen.queryByText(/Signed in as/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });
});
