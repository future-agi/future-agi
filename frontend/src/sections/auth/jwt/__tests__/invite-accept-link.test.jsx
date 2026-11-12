/* eslint-disable react/prop-types */
import React from "react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigationType,
} from "react-router-dom";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { handleError } from "src/utils/queryErrorHandler";
import JwtLoginView from "../jwt-login-view";
import InviteAcceptedPage from "../invite-accepted";

// The link in an invite email opens /auth/jwt/invitation/accept/:uuid/:token,
// which renders the sign-in view while it asks the backend about the invite.
// A link that was already used (or cancelled, expired or malformed) answers
// 400, and the person must land on the page that says the link no longer
// works and what to do next, not on a plain sign-in form.

const h = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: h.get, post: vi.fn() },
  endpoints: {
    invite: {
      accept_invitation: (uid, token) =>
        `/accounts/accept-invitation/${uid}/${token}/`,
    },
    auth: {},
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ login: vi.fn(), logout: vi.fn() }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: true, isCloud: false, isSuccess: true }),
  usePostLoginPath: () => "/dashboard/get-started",
}));
vi.mock("@simplewebauthn/browser", () => ({
  browserSupportsWebAuthn: () => false,
  startAuthentication: vi.fn(),
}));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn(async () => "token"),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/RegionSelect", () => ({ default: () => null }));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

const UID = "dWlk";
const TOKEN = "tok-en";
const ACCEPT = `/auth/jwt/invitation/accept/${UID}/${TOKEN}`;
const SET_PASSWORD = `/auth/jwt/invitation/set-password/${UID}/${TOKEN}`;

// The rejection the shared axios client gives callers for a 400
// (src/utils/axios.js: the response body plus statusCode).
const refusal = (result) => ({ status: false, result, statusCode: 400 });

function Where() {
  return (
    <output aria-label="Current page">
      {`${useLocation().pathname} ${useNavigationType()}`}
    </output>
  );
}

// The app's query client reports failed requests the same way (app.jsx).
function openAcceptLink() {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <MemoryRouter initialEntries={[ACCEPT]}>
          <Routes>
            <Route
              path="/auth/jwt/invitation/accept/:uuid/:token"
              element={<JwtLoginView />}
            />
            <Route
              path="/auth/jwt/invitation/set-password/:uuid/:token"
              element={<InviteAcceptedPage />}
            />
          </Routes>
          <Where />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.get.mockReset();
});
afterEach(cleanup);

describe("Opening an invite link that no longer works, signed out", () => {
  it.each([
    [
      "already used, cancelled or expired",
      "This invitation has been cancelled or expired.",
    ],
    ["malformed", "Invitation link is invalid or has expired."],
  ])(
    "a link that is %s ends on the page that explains it, with its two actions",
    async (_kind, result) => {
      h.get.mockRejectedValue(refusal(result));
      openAcceptLink();

      expect(
        await screen.findByRole("heading", {
          name: "This invite link no longer works",
        }),
      ).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
        "href",
        "/auth/jwt/login",
      );
      expect(
        screen.getByRole("link", { name: "Reset your password" }),
      ).toHaveAttribute("href", "/auth/jwt/forget-password");
      // The accept URL is replaced, so Back does not reopen it.
      expect(screen.getByLabelText("Current page")).toHaveTextContent(
        `${SET_PASSWORD} REPLACE`,
      );
      // Not the plain sign-in form, and no error toast over the page.
      expect(screen.queryByText("Welcome to FutureAGI")).toBeNull();
      expect(screen.queryByLabelText("Email address")).toBeNull();
      expect(screen.queryByText("Failed to accept invitation")).toBeNull();
      expect(screen.queryByText(result)).toBeNull();
    },
  );
});

describe("Opening an invite link that still works", () => {
  it("goes on to set a password, as before", async () => {
    h.get.mockResolvedValue({
      data: { email: "new@acme.test", org_name: "Acme" },
    });
    openAcceptLink();

    expect(await screen.findByText("Set Your Password")).toBeInTheDocument();
    expect(
      screen.getByText("Welcome to Acme! Set a password to get started."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Set Password & Continue" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Current page")).toHaveTextContent(
      SET_PASSWORD,
    );
  });

  it("tells someone signed in as another person to sign out first, as before", async () => {
    h.get.mockRejectedValue({
      status: false,
      statusCode: 403,
      code: "authenticated_user_mismatch",
      error: "You are logged in as a different user. Please logout first.",
    });
    openAcceptLink();

    expect(await screen.findByText("Wrong Account")).toBeInTheDocument();
    expect(
      screen.getByText(
        "You are logged in as a different user. Please logout first.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Logout & Try Again" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Current page")).toHaveTextContent(
      SET_PASSWORD,
    );
  });
});
