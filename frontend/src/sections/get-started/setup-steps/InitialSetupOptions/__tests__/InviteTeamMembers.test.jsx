/* eslint-disable react/prop-types */
// WALK-A (oss edition): "'Invite team members' shows only a
// full-width 'Continue' button with no fields, plus 'Complete the setup'.
// It is unclear what 'Continue' does." Where this step offers no invite
// form, it says where teammates are invited and links there. Where it does
// (ee, cloud, or while the edition is not confirmed), the form stays.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderWithRouter, screen, waitFor } from "src/utils/test-utils";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const edition = vi.hoisted(() => ({ answer: null }));
const auth = vi.hoisted(() => ({ role: "Owner" }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
// What the auth provider gives: the role it reports is the default
// workspace role, else the organization role; a level it does not know is
// empty.
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "person-a", organization_role: auth.role },
    role: auth.role,
    orgLevel: null,
    wsLevel: null,
    effectiveLevel: null,
  }),
}));
// What the organization provider gives: the organization role from
// user-info.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationId: "org-a",
    currentOrganizationRole: auth.role,
  }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  handleOnDocsClicked: vi.fn(),
  Events: {},
  PropertyName: {},
}));

const { endpoints } = await import("src/utils/axios");
const { default: InviteTeamMembers } = await import("../InviteTeamMembers");

const ok = (result) => Promise.resolve({ data: { status: true, result } });

beforeEach(() => {
  auth.role = "Owner";
  http.get.mockReset();
  http.post.mockReset();
  http.get.mockImplementation((url) => {
    if (url === endpoints.settings.v2.deploymentInfo) return edition.answer();
    if (url === endpoints.auth.create_org)
      return ok({ org_name: "Walk org", results: [] });
    if (url === endpoints.settings.teams.getMemberList)
      return ok({ results: [] });
    return ok({});
  });
});

const renderStep = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={client}>
      <InviteTeamMembers />
    </QueryClientProvider>,
    { route: "/dashboard/get-started?tab=inviteTeamMembers" },
  );
};

const deploymentInfoAnswered = () =>
  waitFor(() =>
    expect(
      http.get.mock.calls.some(
        ([url]) => url === endpoints.settings.v2.deploymentInfo,
      ),
    ).toBe(true),
  );

describe("Get started: Invite team members", () => {
  it("oss: says teammates are invited from Settings > Members and links there", async () => {
    edition.answer = () => ok({ mode: "oss" });
    renderStep();
    await deploymentInfoAnswered();

    expect(
      await screen.findByText("Invite teammates from Settings > Members."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Members" })).toHaveAttribute(
      "href",
      "/dashboard/settings/user-management",
    );
    // No unexplained 'Continue' and no empty form.
    expect(
      screen.queryByRole("button", { name: "Continue" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Invite people to collaborate in Future AGI"),
    ).not.toBeInTheDocument();
  });

  it("guard: oss, an admin: says teammates are invited from Settings > Members and links there", async () => {
    // Admins open Members too, and the step is listed for them as for
    // everyone.
    auth.role = "Admin";
    edition.answer = () => ok({ mode: "oss" });
    renderStep();
    await deploymentInfoAnswered();

    expect(
      await screen.findByText("Invite teammates from Settings > Members."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Members" })).toHaveAttribute(
      "href",
      "/dashboard/settings/user-management",
    );
    expect(
      screen.queryByText(
        "Ask an owner or admin of your organization to invite teammates from Settings > Members.",
      ),
    ).not.toBeInTheDocument();
  });

  it("oss, someone who cannot open Members: says who invites, with no link", async () => {
    // The step is listed for every reader; a Member cannot invite anywhere,
    // so they are told who can.
    auth.role = "Member";
    edition.answer = () => ok({ mode: "oss" });
    renderStep();
    await deploymentInfoAnswered();

    expect(
      await screen.findByText(
        "Ask an owner or admin of your organization to invite teammates from Settings > Members.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Go to Members" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue" }),
    ).not.toBeInTheDocument();
  });

  // Edition guards (pass at the base too): where the step offers the invite
  // form, it keeps it and shows no Members link.
  it.each([["ee"], ["cloud"]])(
    "guard: %s keeps the invite form",
    async (mode) => {
      edition.answer = () => ok({ mode });
      renderStep();
      await deploymentInfoAnswered();

      expect(
        await screen.findByText("Invite people to collaborate in Future AGI"),
      ).toBeInTheDocument();
      expect(screen.getByText("Add members")).toBeInTheDocument();
      expect(
        screen.queryByRole("link", { name: "Go to Members" }),
      ).not.toBeInTheDocument();
    },
  );

  it("guard: an edition that is not confirmed keeps the invite form", async () => {
    edition.answer = () =>
      Promise.reject(Object.assign(new Error("offline"), { statusCode: 503 }));
    renderStep();
    // The edition probe retries once, then gives up.
    await waitFor(
      () =>
        expect(
          http.get.mock.calls.filter(
            ([url]) => url === endpoints.settings.v2.deploymentInfo,
          ),
        ).toHaveLength(2),
      { timeout: 5000 },
    );
    await act(async () => {});

    expect(
      await screen.findByText("Invite people to collaborate in Future AGI"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Go to Members" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the step's own title and line", async () => {
    edition.answer = () => ok({ mode: "oss" });
    renderStep();

    expect(screen.getByText("Invite team members")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Invite teammates to your organization so they can sign in and work with you.",
      ),
    ).toBeInTheDocument();
  });
});
