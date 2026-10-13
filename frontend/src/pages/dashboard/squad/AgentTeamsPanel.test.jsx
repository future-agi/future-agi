import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AgentTeamsPanel from "./AgentTeamsPanel";

const team = {
  id: "agtm_team-1",
  name: "Research",
  purpose: "Investigate incidents",
  agentIds: ["agent-1"],
  useMinRole: "member",
  editMinRole: "admin",
};

function makeClient() {
  return {
    listAgentTeams: vi
      .fn()
      .mockResolvedValue({ available: true, teams: [team], canCreate: true }),
    createAgentTeam: vi
      .fn()
      .mockResolvedValue({
        available: true,
        team: { ...team, id: "agtm_team-2", name: "Support" },
      }),
    updateAgentTeam: vi.fn().mockResolvedValue({ available: true, team }),
    deleteAgentTeam: vi.fn().mockResolvedValue({ available: true, teams: [] }),
  };
}

describe("Omega Squad agent teams panel", () => {
  it("creates, edits, and disbands tenant agent teams through the remote client", async () => {
    const client = makeClient();
    vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    render(
      <AgentTeamsPanel
        client={client}
        agents={[
          { id: "agent-1", name: "Researcher" },
          { id: "agent-2", name: "Writer" },
        ]}
      />,
    );

    expect((await screen.findAllByText("Research")).length).toBeGreaterThan(0);
    expect(screen.getByText("Researcher")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Create team" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      "Support",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Purpose" }),
      "Resolve support tickets",
    );
    const createButtons = screen.getAllByRole("button", {
      name: "Create team",
      exact: true,
    });
    await userEvent.click(createButtons[createButtons.length - 1]);
    await waitFor(() =>
      expect(client.createAgentTeam).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Support",
          purpose: "Resolve support tickets",
          agentIds: [],
        }),
        expect.stringMatching(/^team-create-/),
      ),
    );

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const purpose = screen.getByRole("textbox", { name: "Purpose" });
    await userEvent.clear(purpose);
    await userEvent.type(purpose, "Investigate production incidents");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(client.updateAgentTeam).toHaveBeenCalledWith(
        "agtm_team-1",
        expect.objectContaining({
          purpose: "Investigate production incidents",
        }),
        expect.stringMatching(/^team-update-agtm_team-1-/),
      ),
    );

    await userEvent.click(screen.getByRole("button", { name: "Disband" }));
    await waitFor(() =>
      expect(client.deleteAgentTeam).toHaveBeenCalledWith(
        "agtm_team-1",
        expect.stringMatching(/^team-delete-agtm_team-1-/),
      ),
    );
  });
});
