/* eslint-disable react/prop-types */
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import InviteAcceptedPage from "../invite-accepted";

// An invite link that was already used, cancelled or has expired: the accept
// endpoint answers 400 with the same refusal for all three
// (accounts/views/signup.py: the invite is no longer Pending, "This
// invitation has been cancelled or expired."), and a malformed or tampered
// link answers 400 "Invitation link is invalid or has expired.". The page
// cannot tell them apart, so it shows one message that is true for each and
// says what to do next: sign in, or ask an administrator for a new invite.

const h = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: h.get, post: vi.fn() },
  endpoints: {
    invite: {
      accept_invitation: (uid, token) =>
        `/accounts/accept-invitation/${uid}/${token}/`,
    },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ login: vi.fn(), logout: vi.fn() }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// The rejection the shared axios client gives callers for a 400
// (src/utils/axios.js: the response body plus statusCode).
const refusal = (result) => ({ status: false, result, statusCode: 400 });

function openInviteLink() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter
        initialEntries={["/auth/jwt/invitation/set-password/dWlk/tok-en"]}
      >
        <Routes>
          <Route
            path="/auth/jwt/invitation/set-password/:uuid/:token"
            element={<InviteAcceptedPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.get.mockReset();
});
afterEach(cleanup);

describe("An invite link that can no longer be used", () => {
  it.each([
    [
      "used, cancelled or expired",
      "This invitation has been cancelled or expired.",
    ],
    ["malformed", "Invitation link is invalid or has expired."],
  ])("explains a %s link and what to do next", async (_kind, result) => {
    h.get.mockRejectedValue(refusal(result));
    openInviteLink();

    expect(
      await screen.findByRole("heading", {
        name: "This invite link no longer works",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "It may have been used already, cancelled, or it may have expired.",
      ),
    ).toBeInTheDocument();
    // True in every edition, whatever way the person signs in.
    expect(
      screen.getByText(
        "If you already accepted this invite, sign in. Otherwise, ask an administrator of the organization for a new invite.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/email and password/)).toBeNull();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/auth/jwt/login",
    );
    expect(
      screen.getByRole("link", { name: "Reset your password" }),
    ).toHaveAttribute("href", "/auth/jwt/forget-password");
    // No password form for a link that cannot be accepted.
    expect(
      screen.queryByRole("button", { name: "Set Password & Continue" }),
    ).toBeNull();
    expect(screen.queryByText("Invite Link Expired")).toBeNull();
  });
});
