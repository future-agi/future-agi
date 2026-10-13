import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { FleetHealthPanel } from "./OmegaSquadPage";

describe("Omega Squad fleet health", () => {
  it("renders server-provided activity detail and explicit stale/unknown states", () => {
    render(
      <FleetHealthPanel
        loading={false}
        fleet={{
          agents: [
            {
              id: "agent-1",
              name: "Researcher",
              status: "working",
              currentWork: "Review incidents",
              updatedAt: new Date().toISOString(),
            },
            {
              id: "agent-2",
              name: "Writer",
              status: "offline",
              reason: "worker_unavailable",
            },
          ],
          health: {
            total: 2,
            byStatus: {
              idle: 0,
              working: 1,
              paused: 0,
              needs_attention: 0,
              offline: 1,
            },
          },
        }}
      />,
    );

    expect(screen.getByText("Recent activity")).toBeInTheDocument();
    expect(
      screen.getByText(/Researcher · Review incidents/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Writer · worker_unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/timestamp unknown/)).toBeInTheDocument();
  });
});
