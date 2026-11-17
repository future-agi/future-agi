import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { act, fireEvent, render, screen } from "@testing-library/react";
import {
  MemoryRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthContext } from "src/auth/context/jwt/auth-context";
import GuestGuard from "src/auth/guard/guest-guard";
import { SIGN_OUT_LANDING, signOutToSignIn } from "src/auth/sign-out";

// DEMO-FIX-2 item 7: signing out lands on the sign-in page, never the setup
// wizard. The owner demo signed out into "/", which a self-hosted build sends
// to /setup in a browser that never ran the first-run checks.

describe("signOutToSignIn", () => {
  it("signs out first, then replaces the page with the sign-in page", async () => {
    const order = [];
    const logout = vi.fn(() => {
      order.push("logout");
      return Promise.resolve();
    });
    const navigate = vi.fn(() => order.push("navigate"));
    await signOutToSignIn(logout, navigate);
    expect(order).toEqual(["logout", "navigate"]);
    expect(navigate).toHaveBeenCalledWith("/auth/jwt/login", { replace: true });
    expect(SIGN_OUT_LANDING).toBe("/auth/jwt/login");
  });

  it("still lands on the sign-in page when the server call fails or logout throws", async () => {
    const navigate = vi.fn();
    await expect(
      signOutToSignIn(() => Promise.reject(new Error("502")), navigate),
    ).resolves.toBeUndefined();
    await expect(
      signOutToSignIn(() => {
        throw new Error("storage");
      }, navigate),
    ).resolves.toBeUndefined();
    expect(navigate).toHaveBeenCalledTimes(2);
    expect(navigate).toHaveBeenLastCalledWith("/auth/jwt/login", {
      replace: true,
    });
  });
});

// The real GuestGuard on the sign-in page, and "/" sending to /setup as a
// self-hosted build does before the first-run checks ran in this browser.
function FakeAuth({ children, onLogout }) {
  const [authenticated, setAuthenticated] = useState(true);
  const logout = useCallback(() => {
    // As auth-provider.jsx: the session is cleared before the first await.
    setAuthenticated(false);
    onLogout();
    return new Promise((resolve) => setTimeout(resolve, 20));
  }, [onLogout]);
  const value = useMemo(
    () => ({ authenticated, loading: false, user: {}, logout }),
    [authenticated, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

FakeAuth.propTypes = { children: PropTypes.node, onLogout: PropTypes.func };

function SignOutButton() {
  const navigate = useNavigate();
  const { logout } = React.useContext(AuthContext);
  return (
    <button type="button" onClick={() => signOutToSignIn(logout, navigate)}>
      Logout
    </button>
  );
}

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname + location.search}</div>;
}

describe("signing out in the app", () => {
  it("lands on /auth/jwt/login with no detour through / or the setup wizard, and the sign-in page keeps it there", async () => {
    const seen = [];
    function Track() {
      const location = useLocation();
      seen.push(location.pathname);
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/dashboard/console/chat"]}>
        <FakeAuth onLogout={() => undefined}>
          <Track />
          <Where />
          <Routes>
            <Route path="/" element={<Navigate to="/setup" replace />} />
            <Route path="/setup" element={<h1>Set up Future AGI</h1>} />
            <Route
              path="/auth/jwt/login"
              element={
                <GuestGuard>
                  <h1>Sign in</h1>
                </GuestGuard>
              }
            />
            <Route path="/dashboard/*" element={<SignOutButton />} />
          </Routes>
        </FakeAuth>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Logout" }));
    await act(() => new Promise((resolve) => setTimeout(resolve, 60)));
    expect(screen.getByTestId("where")).toHaveTextContent("/auth/jwt/login");
    expect(
      screen.getByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(seen).not.toContain("/setup");
    expect(seen).not.toContain("/");
    // GuestGuard never bounced a still-signed-in session back to the dashboard.
    expect(seen.at(-1)).toBe("/auth/jwt/login");
  });
});
