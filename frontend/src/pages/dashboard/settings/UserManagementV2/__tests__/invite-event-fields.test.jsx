import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import { Events } from "src/utils/Mixpanel";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    get: vi.fn(),
    post: vi.fn(),
    enqueueSnackbar: vi.fn(),
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      register: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: mocks.get, post: mocks.post },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: mocks.enqueueSnackbar,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { email: "admin@example.test", organization_role: "Member" },
    orgLevel: null,
  }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isSuccess: true }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({
    data: [{ id: "ws-synthetic-1", name: "Synthetic Workspace" }],
  }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import AllActionForm from "../AllActionForm";

const invitees = [
  "synthetic_invitee_1d7e@example.test",
  "synthetic_invitee_2e8f@example.test",
];

describe("workspace invite event fields", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.post.mockResolvedValue({
      data: { result: { invited: invitees, errors: [] } },
    });
  });

  it("sends the number of invited addresses and the role level", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AllActionForm
          openActionForm={{ action: "invite-workspace-user" }}
          onClose={vi.fn()}
          workspaceId="ws-synthetic-1"
        />
      </QueryClientProvider>,
    );

    const input = await screen.findByPlaceholderText("Emails, comma separated");
    await user.type(input, `${invitees[0]},${invitees[1]},`);
    const send = screen.getByRole("button", { name: "Send Invite" });
    await waitFor(() => expect(send).toBeEnabled());
    await user.click(send);

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post).toHaveBeenCalledWith(endpoints.rbac.inviteCreate, {
      emails: invitees,
      org_level: 1,
      workspace_access: [{ workspace_id: "ws-synthetic-1", level: 3 }],
    });
    const events = mocks.mixpanel.track.mock.calls.filter(
      ([eventName]) => eventName === Events.workspaceSendInviteClicked,
    );
    expect(events).toHaveLength(1);
    expect(events[0][1]).toEqual({
      click: "click",
      invite_count: 2,
      orgLevel: 1,
    });
    const tracked = JSON.stringify(mocks.mixpanel.track.mock.calls);
    invitees.forEach((email) => expect(tracked).not.toContain(email));
  });
});
