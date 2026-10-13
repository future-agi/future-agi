import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import GovernancePanel from "./GovernancePanel";

describe("Omega Squad governance panel", () => {
  it("reads members, invites, activity, and plan without rendering invite tokens", async () => {
    const client = {
      listOrganizations: vi
        .fn()
        .mockResolvedValue({
          available: true,
          orgs: [{ id: "org-1", name: "Acme", role: "owner" }],
        }),
      listOrganizationMembers: vi
        .fn()
        .mockResolvedValue({
          available: true,
          members: [
            { accountId: "acct-1", email: "owner@example.com", role: "owner" },
          ],
        }),
      listOrganizationInvites: vi
        .fn()
        .mockResolvedValue({
          available: true,
          invites: [
            {
              id: "invite-1",
              email: "member@example.com",
              role: "member",
              expiresAt: "2026-11-01T00:00:00.000Z",
              token: "do-not-render",
            },
          ],
        }),
      getOrganizationActivity: vi
        .fn()
        .mockResolvedValue({
          available: true,
          activity: [{ type: "member_joined", at: "2026-10-12T00:00:00.000Z" }],
        }),
      getOrganizationPlan: vi
        .fn()
        .mockResolvedValue({
          available: true,
          plan: { planId: "team", status: "active" },
        }),
      createOrganizationInvite: vi
        .fn()
        .mockResolvedValue({ available: true, token: "one-time-token" }),
      revokeOrganizationInvite: vi.fn().mockResolvedValue({ available: true }),
      updateOrganizationMemberRole: vi
        .fn()
        .mockResolvedValue({ available: true }),
      removeOrganizationMember: vi.fn().mockResolvedValue({ available: true }),
    };
    render(<GovernancePanel client={client} />);
    expect(
      await screen.findByText("Organization governance"),
    ).toBeInTheDocument();
    expect(await screen.findByText("owner@example.com")).toBeInTheDocument();
    expect(screen.getByText("member@example.com")).toBeInTheDocument();
    expect(screen.getByText("Plan: team")).toBeInTheDocument();
    expect(screen.queryByText("do-not-render")).not.toBeInTheDocument();
  });

  it("reveals a manually issued invite token only after the explicit invite action", async () => {
    const client = {
      listOrganizations: vi
        .fn()
        .mockResolvedValue({
          available: true,
          orgs: [{ id: "org-1", name: "Acme", role: "owner" }],
        }),
      listOrganizationMembers: vi
        .fn()
        .mockResolvedValue({ available: true, members: [] }),
      listOrganizationInvites: vi
        .fn()
        .mockResolvedValue({ available: true, invites: [] }),
      getOrganizationActivity: vi
        .fn()
        .mockResolvedValue({ available: true, activity: [] }),
      getOrganizationPlan: vi
        .fn()
        .mockResolvedValue({ available: true, plan: { planId: "team" } }),
      createOrganizationInvite: vi
        .fn()
        .mockResolvedValue({ available: true, token: "one-time-token" }),
    };
    render(<GovernancePanel client={client} />);
    const email = await screen.findByRole("textbox", { name: "Invite email" });
    await userEvent.type(email, "new@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Invite" }));
    await waitFor(() =>
      expect(client.createOrganizationInvite).toHaveBeenCalledWith(
        "org-1",
        { email: "new@example.com", role: "member", manualDelivery: true },
        expect.stringMatching(/^squad-invite-/),
      ),
    );
    expect(await screen.findByText("one-time-token")).toBeInTheDocument();
  });
});
