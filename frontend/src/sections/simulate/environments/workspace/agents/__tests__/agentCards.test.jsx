import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import AgentHeroCard from "../AgentHeroCard";
import VersionHistoryCard from "../VersionHistoryCard";
import AgentCard from "../AgentCard";
import PromoteDialog from "../PromoteDialog";
import DivergenceBanner from "../DivergenceBanner";

const VERSIONS = [
  { id: "v1", label: "v1", connectedAt: "2026-01-01T10:00:00Z", note: "First version connected" },
  { id: "v2", label: "v2", connectedAt: "2026-02-01T10:00:00Z", note: "Tuned prompt" },
  { id: "v3", label: "v3", connectedAt: "2026-03-01T10:00:00Z", note: "Added a tool" },
];

const AGENT = {
  id: "source",
  typeId: "text",
  isSource: true,
  activeVersionId: "v2",
  values: { repoUrl: "https://github.com/acme/support-bot" },
  versions: VERSIONS,
};

describe("AgentHeroCard", () => {
  it("shows the agent name and type line", () => {
    render(<AgentHeroCard agent={AGENT} onAddVersion={vi.fn()} />);
    expect(screen.getByText("support-bot")).toBeInTheDocument();
    expect(screen.getByText(/Chat/)).toBeInTheDocument();
  });

  it("fires onAddVersion when Add new version is clicked", async () => {
    const user = userEvent.setup();
    const onAddVersion = vi.fn();
    render(<AgentHeroCard agent={AGENT} onAddVersion={onAddVersion} />);
    await user.click(screen.getByRole("button", { name: /add new version/i }));
    expect(onAddVersion).toHaveBeenCalledTimes(1);
  });
});

describe("VersionHistoryCard", () => {
  it("renders every version with the active one marked", () => {
    render(<VersionHistoryCard agent={AGENT} onSetActiveVersion={vi.fn()} />);
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText("v3")).toBeInTheDocument();
    // Active version (v2) carries the ACTIVE pill; the other rows do not offer it.
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
  });

  it("offers Roll back for older versions and Set active for newer ones", async () => {
    const user = userEvent.setup();
    const onSetActiveVersion = vi.fn();
    render(<VersionHistoryCard agent={AGENT} onSetActiveVersion={onSetActiveVersion} />);

    // v3 is newer than the active v2 -> "Set active"; clicking fires with v3.
    await user.click(screen.getByRole("button", { name: /^set active$/i }));
    expect(onSetActiveVersion).toHaveBeenCalledWith("v3");

    onSetActiveVersion.mockClear();
    // v1 is older than the active v2 -> "Roll back to this"; clicking fires with v1.
    await user.click(screen.getByRole("button", { name: /roll back to this/i }));
    expect(onSetActiveVersion).toHaveBeenCalledWith("v1");
  });

  it("does not offer a set-active action on the active version", () => {
    render(<VersionHistoryCard agent={AGENT} onSetActiveVersion={vi.fn()} />);
    // Exactly two non-active rows -> two actions total (one Set active, one Roll back).
    expect(screen.getByRole("button", { name: /^set active$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /roll back to this/i })).toBeInTheDocument();
  });
});

describe("AgentCard", () => {
  const ADDITIONAL = {
    id: "agent-2", typeId: "text", isSource: false,
    activeVersionId: "v1",
    values: { endpoint: "https://api.acme.dev/agent" },
    versions: [{ id: "v1", label: "v1", connectedAt: "2026-01-01T10:00:00Z" }],
  };

  it("marks the source with ENV SOURCE and fires Set active for an inactive additional agent", async () => {
    const user = userEvent.setup();
    const onSetActive = vi.fn();
    render(
      <AgentCard
        agent={ADDITIONAL} isActive={false} expanded={false}
        onToggleExpand={vi.fn()} onSetActive={onSetActive}
        onPromote={vi.fn()} onRemove={vi.fn()}
        onAddVersion={vi.fn()} onSetActiveVersion={vi.fn()}
      />
    );
    await user.click(screen.getByRole("button", { name: /^set active$/i }));
    expect(onSetActive).toHaveBeenCalledTimes(1);
  });

  it("toggles the detail from the chevron without bubbling to a set-active", async () => {
    const user = userEvent.setup();
    const onToggleExpand = vi.fn();
    render(
      <AgentCard
        agent={AGENT} isActive expanded={false}
        onToggleExpand={onToggleExpand} onSetActive={vi.fn()}
        onPromote={vi.fn()} onRemove={vi.fn()}
        onAddVersion={vi.fn()} onSetActiveVersion={vi.fn()}
      />
    );
    await user.click(screen.getByRole("button", { name: /expand details/i }));
    expect(onToggleExpand).toHaveBeenCalledTimes(1);
  });
});

describe("PromoteDialog", () => {
  it("fires onConfirm when Update environment is clicked", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<PromoteDialog agent={AGENT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await user.click(screen.getByRole("button", { name: /update environment/i }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("does not render when no agent is being promoted", () => {
    render(<PromoteDialog agent={null} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.queryByText(/update environment from this agent/i)).toBeNull();
  });
});

describe("DivergenceBanner", () => {
  it("shows the active label and fires restore and promote", async () => {
    const user = userEvent.setup();
    const onPromote = vi.fn();
    const onRestore = vi.fn();
    render(<DivergenceBanner activeLabel="v3" onPromote={onPromote} onRestore={onRestore} />);

    expect(screen.getByText("v3")).toBeInTheDocument();
    const banner = screen.getByText(/scenarios and rules still come from the source/i);
    expect(banner).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /restore to source/i }));
    expect(onRestore).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /promote to source/i }));
    expect(onPromote).toHaveBeenCalledTimes(1);
  });
});
