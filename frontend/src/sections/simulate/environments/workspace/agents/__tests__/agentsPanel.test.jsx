import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { render } from "src/utils/test-utils";
import AgentsPanel from "../AgentsPanel";

// A3's AddAgentDrawer already covers its own form (reach picker, field
// validation, record building). Here we only need it to hand AgentsPanel a
// saved record so the mint-v2 recipe is what is under test — stub it to a bare
// button.
vi.mock("../AddAgentDrawer", () => ({
  default: ({ open, onAdd }) =>
    open ? (
      <button
        type="button"
        onClick={() =>
          onAdd({
            values: { endpoint: "https://v2.example.com" },
            via: "Probed at https://v2.example.com",
            connectedAt: "2026-09-17T00:00:00.000Z",
            note: "second build",
          })
        }
      >
        stub-save-version
      </button>
    ) : null,
}));

const twoVersionAgent = () => ({
  typeId: "voice",
  values: { endpoint: "https://v2.example.com" },
  activeVersionId: "v2",
  versions: [
    {
      id: "v1", label: "v1",
      values: { endpoint: "https://v1.example.com" },
      via: "Probed at v1", connectedAt: "2026-09-01T00:00:00.000Z", note: "First build",
    },
    {
      id: "v2", label: "v2",
      values: { endpoint: "https://v2.example.com" },
      via: "Probed at v2", connectedAt: "2026-09-10T00:00:00.000Z", note: "Second build",
    },
  ],
});

const oneVersionAgent = () => ({
  typeId: "voice",
  values: { endpoint: "https://v1.example.com" },
  activeVersionId: "v1",
  versions: [
    {
      id: "v1", label: "v1",
      values: { endpoint: "https://v1.example.com" },
      via: "Probed at v1", connectedAt: "2026-09-01T00:00:00.000Z", note: "First build",
    },
  ],
});

const stateWith = (agent) => ({
  agent,
  additionalAgents: [],
  activeAgentId: null,
  agentVersions: (agent?.versions || []).map((v) => ({ id: v.id, label: v.label, note: v.note })),
  activeAgentVersion: agent?.activeVersionId,
  scenarios: [],
});

// The "via Overview 'Manage versions'" entry path is commented out in
// OverviewPanel (the agent card + version drawer, to be picked up later), so its
// two tests were removed. AgentsPanel itself is still exercised directly below.

describe("AgentsPanel recipes", () => {
  it("fires the set-active patch when a non-active version is picked", async () => {
    const user = userEvent.setup();
    const patch = vi.fn();
    render(<AgentsPanel envState={stateWith(twoVersionAgent())} patch={patch} />);

    // v2 is active, so v1 offers a roll-back.
    await user.click(screen.getByRole("button", { name: /roll back to this/i }));

    expect(patch).toHaveBeenCalledTimes(1);
    const arg = patch.mock.calls[0][0];
    expect(arg.activeAgentVersion).toBe("v1");
    expect(arg.agent.activeVersionId).toBe("v1");
    // applyActiveVersion mirrored v1's connection fields to the top level.
    expect(arg.agent.values).toEqual({ endpoint: "https://v1.example.com" });
  });

  it("mints v2 through the add-version flow", async () => {
    const user = userEvent.setup();
    const patch = vi.fn();
    render(<AgentsPanel envState={stateWith(oneVersionAgent())} patch={patch} />);

    await user.click(screen.getByRole("button", { name: /add new version/i }));
    await user.click(screen.getByRole("button", { name: /stub-save-version/i }));

    expect(patch).toHaveBeenCalledTimes(1);
    const arg = patch.mock.calls[0][0];
    expect(arg.agentVersions).toHaveLength(2);
    expect(arg.activeAgentVersion).toBe("v2");
    expect(arg.agent.activeVersionId).toBe("v2");
    expect(arg.agent.versions).toHaveLength(2);
  });
});

describe("AgentsPanel — template lock (read-only until forked)", () => {
  const LOCK_TOOLTIP = "Fork this environment to edit.";

  // OverviewPanel gates the whole version-management drawer behind !locked, so
  // this is only reachable by a direct render — but the panel still defends the
  // controls itself.
  it("disables Add new version and roll-back with the fork tooltip", () => {
    render(<AgentsPanel envState={stateWith(twoVersionAgent())} patch={vi.fn()} locked />);

    expect(screen.getByRole("button", { name: /add new version/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /roll back to this/i })).toBeDisabled();
    expect(screen.getAllByLabelText(LOCK_TOOLTIP).length).toBeGreaterThan(0);
  });

  it("keeps those controls enabled when not locked", () => {
    render(<AgentsPanel envState={stateWith(twoVersionAgent())} patch={vi.fn()} locked={false} />);

    expect(screen.getByRole("button", { name: /add new version/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /roll back to this/i })).toBeEnabled();
    expect(screen.queryByLabelText(LOCK_TOOLTIP)).toBeNull();
  });
});
