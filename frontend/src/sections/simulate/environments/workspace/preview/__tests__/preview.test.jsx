import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { ReadinessView } from "../ReadinessRows";
import DroppedCases from "../DroppedCases";

describe("Feature 3 preview", () => {
  it("shows each readiness row, the sub-agent checklist and each worker's boundary", () => {
    render(
      <ReadinessView
        report={{
          blockers: 1,
          rows: [
            {
              id: "hidden_material",
              label: "Hidden material",
              status: "partial",
              reason: "Not proved yet",
              evidence: [],
            },
            {
              id: "children",
              label: "Sub-agents",
              status: "partial",
              reason: null,
              evidence: [{ check: "stable_ids", result: "not_seen" }],
            },
          ],
          workers: [
            {
              worker_id: "w1",
              name: "Billing",
              status_label: "Blocked",
              reason: "Reached the world without its channel",
              tools: [{ tool: "refund", boundary: "blocked" }],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText(/1 blocker$/)).toBeInTheDocument();
    expect(
      screen.getByText("Hidden material: Partial · Not proved yet"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Same worker ids across runs:/)).toHaveTextContent(
      "Not seen yet",
    );
    expect(screen.getByText("refund: Blocked")).toBeInTheDocument();
  });

  it("lists dropped cases with why and restored ones with whether proved again", () => {
    render(
      <DroppedCases
        dropped={[
          {
            key: "a",
            name: "Refund",
            reason: "The agent lost refund",
            agent_version: "v3",
          },
        ]}
        restored={[{ key: "b", name: "Cancel", proved: false }]}
      />,
    );
    expect(
      screen.getByText("Refund: The agent lost refund (agent v3)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Cancel: Not proved again yet"),
    ).toBeInTheDocument();
  });
});
