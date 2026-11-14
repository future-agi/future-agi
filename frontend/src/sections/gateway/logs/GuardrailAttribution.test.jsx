import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import RequestDetailDrawer from "./RequestDetailDrawer";

vi.mock("./hooks/useRequestDetail", () => ({
  default: () => ({
    data: {
      result: {
        id: "log-id",
        guardrail_triggered: true,
        guardrail_results: [
          {
            name: "secret-detection",
            action: "log",
            policy_name: "Staging rollout",
            policy_scope: "key",
            mode: "monitor",
            outcome: "would_block",
            would_action: "block",
          },
          {
            name: "pii-detection",
            action: "log",
            policy_name: "Staging rollout",
            policy_scope: "key",
            mode: "monitor",
            outcome: "would_redact",
            would_action: "warn",
          },
        ],
      },
    },
    isLoading: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("../guardrails/FeedbackWidget", () => ({ default: () => null }));

describe("guardrail attribution", () => {
  it("labels the source, mode and monitor outcomes without claiming a block", async () => {
    render(<RequestDetailDrawer logId="log-id" open onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("tab", { name: "Guardrails" }));
    expect(
      screen.getByText("Monitor only, request not changed"),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/Staging rollout · Key/)).toHaveLength(2);
    expect(screen.getAllByText("Monitor")).toHaveLength(2);
    expect(screen.getByText("Would block")).toBeInTheDocument();
    expect(screen.getByText("Would redact")).toBeInTheDocument();
  });
});
