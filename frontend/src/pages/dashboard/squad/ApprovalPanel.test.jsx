import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { ApprovalPanel } from "./OmegaSquadPage";

describe("Omega Squad approval inbox", () => {
  it("shows the actual EE nested approval and decides by its approval id", () => {
    const onResolve = vi.fn();
    render(
      <ApprovalPanel
        result={{
          available: true,
          approvals: [
            {
              approval: {
                id: "approval-1",
                title: "Send customer report",
                detail: "Send the approved report to the customer.",
                risk: "high",
                proposedAction: { kind: "tool", summary: "Send report" },
              },
              conversationId: "conversation-1",
              taskId: "task-1",
              agentId: "agent-1",
              stepIndex: 2,
            },
          ],
        }}
        busyId=""
        onResolve={onResolve}
      />,
    );

    expect(screen.getByText("Send customer report")).toBeInTheDocument();
    expect(screen.getByText(/Send the approved report/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onResolve.mock.calls).toEqual([
      ["approval-1", "approve"],
      ["approval-1", "deny"],
    ]);
  });

  it("keeps a nested approval without a decision id disabled instead of using its task id", () => {
    render(
      <ApprovalPanel
        result={{
          available: true,
          approvals: [
            {
              approval: { title: "Malformed approval" },
              taskId: "task-not-an-approval",
            },
          ],
        }}
        busyId=""
        onResolve={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });
});
