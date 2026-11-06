import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "src/utils/test-utils";
import ManageTeam from "../ManageTeam";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    settings: { teams: { getMemberList: "/accounts/team/users/" } },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "owner-1" } }),
}));
vi.mock("../InviteUserDrawer", () => ({ default: () => null }));
vi.mock("../DeleteTeamMember", () => ({ default: () => null }));
vi.mock("../NoMember", () => ({
  default: () => <div>No members yet</div>,
}));

const owner = {
  id: "owner-1",
  email: "owner@example.com",
  organization_role: "Owner",
};
const member = {
  id: "member-1",
  email: "newhire@example.com",
  organization_role: "Member",
};
const response = (members) => ({
  data: {
    status: true,
    result: {
      org_name: "Test organization",
      results: members,
      total: members.length,
    },
  },
});

function renderTeam() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ManageTeam />
    </QueryClientProvider>,
  );
  return client;
}

describe("Manage Team member response", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders members from the current nested API result", async () => {
    mocks.get.mockResolvedValue(response([owner, member]));
    renderTeam();

    expect(await screen.findByText(member.email)).toBeVisible();
    expect(screen.getByText(owner.email)).toBeVisible();
    expect(screen.queryByText("No members yet")).not.toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith("/accounts/team/users/");
  });

  it("shows the empty state for an empty results array", async () => {
    mocks.get.mockResolvedValue(response([]));
    renderTeam();

    expect(await screen.findByText("No members yet")).toBeVisible();
  });

  it("shows a persisted member when the invite flow refreshes the list", async () => {
    mocks.get
      .mockResolvedValueOnce(response([owner]))
      .mockResolvedValueOnce(response([owner, member]));
    const client = renderTeam();
    expect(await screen.findByText(owner.email)).toBeVisible();
    expect(screen.queryByText(member.email)).not.toBeInTheDocument();

    await act(async () => {
      await client.invalidateQueries({
        queryKey: ["member-list"],
        type: "all",
      });
    });

    expect(await screen.findByText(member.email)).toBeVisible();
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });
});
