import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import InviteAcceptedPage from "../invite-accepted";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  navigate: vi.fn(),
  login: vi.fn(),
  switchOrganization: vi.fn(),
  enqueueSnackbar: vi.fn(),
  authenticated: false,
}));

vi.mock("src/utils/axios", () => ({
  default: { get: h.get, post: h.post },
  endpoints: {
    invite: { accept_invitation: (uid, token) => `/accept/${uid}/${token}/` },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    login: h.login,
    logout: vi.fn(),
    authenticated: h.authenticated,
  }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ switchOrganization: h.switchOrganization }),
}));
vi.mock("src/routes/hooks", () => ({
  useParams: () => ({ uuid: "uid-1", token: "invite.sig" }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: h.enqueueSnackbar }),
}));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => h.navigate,
}));

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InviteAcceptedPage />
    </QueryClientProvider>,
  );

const preview = (existingUser) => ({
  data: {
    valid: true,
    email: "invitee@futureagi.com",
    org_name: "Org B",
    existing_user: existingUser,
  },
});

describe("InviteAcceptedPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.authenticated = false;
    h.post.mockResolvedValue({
      data: { joined: true, org_name: "Org B", organization_id: "org-b-id" },
    });
  });

  it("shows Join button and no password fields when existing_user is true", async () => {
    h.get.mockResolvedValue(preview(true));

    renderPage();

    expect(
      await screen.findByRole("button", { name: "Join Org B" }),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Enter your password")).toBeNull();
  });

  it("posts an empty body on Join and routes to login when logged out", async () => {
    h.get.mockResolvedValue(preview(true));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Join Org B" }));

    await waitFor(() =>
      expect(h.navigate).toHaveBeenCalledWith("/auth/jwt/login"),
    );
    expect(h.post).toHaveBeenCalledWith("/accept/uid-1/invite.sig/", {});
    expect(h.login).not.toHaveBeenCalled();
    expect(h.switchOrganization).not.toHaveBeenCalled();
    expect(h.enqueueSnackbar).toHaveBeenCalledWith("You've joined Org B.", {
      variant: "success",
    });
  });

  it("switches to the joined org and routes to dashboard after Join when already logged in", async () => {
    h.authenticated = true;
    h.get.mockResolvedValue(preview(true));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Join Org B" }));

    await waitFor(() => expect(h.navigate).toHaveBeenCalledWith("/dashboard"));
    expect(h.switchOrganization).toHaveBeenCalledWith("org-b-id");
    expect(h.switchOrganization.mock.invocationCallOrder[0]).toBeLessThan(
      h.navigate.mock.invocationCallOrder[0],
    );
    expect(h.login).not.toHaveBeenCalled();
  });

  it("still shows the password form when existing_user is false", async () => {
    h.get.mockResolvedValue(preview(false));

    renderPage();

    expect(
      await screen.findByPlaceholderText("Enter your password"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Join Org B" })).toBeNull();
  });
});
