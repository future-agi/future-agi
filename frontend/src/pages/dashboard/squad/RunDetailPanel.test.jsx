import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { RunDetailPanel } from "./OmegaSquadPage";

describe("Omega Squad run detail", () => {
  it("shows the authoritative transcript and server error step", () => {
    render(
      <RunDetailPanel
        id="run-1"
        detail={{
          conversation: {
            available: true,
            conversation: {
              id: "run-1",
              status: "error",
              currentStep: "Model provider is not configured.",
            },
            messages: [
              {
                id: "answer",
                conversationId: "run-1",
                role: "agent",
                text: "The partial answer is 42.",
              },
            ],
          },
        }}
        loading={false}
        artifacts={{ evidence: null, proofPack: null }}
        events={[]}
        streamStatus="live"
        busyId=""
        controlReceipt={null}
        followUp=""
        followUpBusy={false}
        onFollowUpChange={vi.fn()}
        onSendFollowUp={vi.fn()}
        onArtifact={vi.fn()}
        onControl={vi.fn()}
        onResolveApproval={vi.fn()}
      />,
    );
    expect(screen.getByText("The partial answer is 42.")).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("alert")
        .some((node) =>
          node.textContent.includes("Model provider is not configured."),
        ),
    ).toBe(true);
  });

  it("renders server-provided budget fields and parked state without recomputing them", () => {
    render(
      <RunDetailPanel
        id="run-1"
        detail={{
          conversation: {
            available: true,
            conversation: {
              status: "parked",
              input: "Investigate",
              parkedReason: "Awaiting approval",
            },
          },
          control: {
            available: true,
            control: {
              runStatus: "parked",
              budget: { maxTokens: 1000 },
              runSpend: { tokens: 240 },
            },
          },
          identity: {
            available: true,
            identity: {
              agentId: "agent-1",
              agentVersion: 2,
              deploymentVersion: 3,
            },
          },
          tree: { available: true, tree: [] },
          approvals: { available: true, approvals: [] },
          observations: { available: true, observations: [] },
        }}
        loading={false}
        artifacts={{ evidence: null, proofPack: null }}
        events={[]}
        streamStatus="live"
        busyId=""
        controlReceipt={null}
        followUp=""
        followUpBusy={false}
        onFollowUpChange={vi.fn()}
        onSendFollowUp={vi.fn()}
        onArtifact={vi.fn()}
        onControl={vi.fn()}
        onResolveApproval={vi.fn()}
      />,
    );

    expect(screen.getByText("Remote run budget")).toBeInTheDocument();
    expect(screen.getByText("maxTokens")).toBeInTheDocument();
    expect(screen.getByText("1000")).toBeInTheDocument();
    expect(screen.getByText(/Awaiting approval/)).toBeInTheDocument();
    expect(
      screen.getByText(/agent-1 · agent v2 · deployment v3/),
    ).toBeInTheDocument();
  });

  it("resolves a nested EE run approval using the approval id", () => {
    const onResolveApproval = vi.fn();
    render(
      <RunDetailPanel
        id="run-1"
        detail={{
          conversation: { available: true, conversation: { status: "parked" } },
          approvals: {
            available: true,
            approvals: [
              {
                approval: {
                  id: "approval-run-1",
                  title: "Publish changes",
                  detail: "Publish the reviewed change.",
                  risk: "high",
                },
                conversationId: "run-1",
                taskId: "task-1",
                agentId: "agent-1",
                stepIndex: 1,
              },
            ],
          },
        }}
        loading={false}
        artifacts={{ evidence: null, proofPack: null }}
        events={[]}
        streamStatus="live"
        busyId=""
        controlReceipt={null}
        followUp=""
        followUpBusy={false}
        onFollowUpChange={vi.fn()}
        onSendFollowUp={vi.fn()}
        onArtifact={vi.fn()}
        onControl={vi.fn()}
        onResolveApproval={onResolveApproval}
      />,
    );
    expect(screen.getByText("Publish changes")).toBeInTheDocument();
    expect(screen.getByText(/Publish the reviewed change/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onResolveApproval.mock.calls).toEqual([
      ["approval-run-1", "approve"],
      ["approval-run-1", "deny"],
    ]);
  });

  it("renders the canonical server task forest and keeps degraded integrity visible", () => {
    render(
      <RunDetailPanel
        id="run-forest"
        detail={{
          conversation: { available: true, conversation: { status: "done" } },
          tree: {
            available: true,
            schemaVersion: "omega-run-task-tree/v1",
            runId: "run-forest",
            integrity: "degraded",
            trees: [
              {
                treeId: "tree-1",
                rootTaskId: "task-1",
                nodes: [
                  {
                    id: "task-1",
                    status: "done",
                    title: "Review support evidence",
                    lineage: { kind: "root", depth: 0 },
                    receipt: { hash: "receipt-hash" },
                  },
                  {
                    id: "task-2",
                    status: "failed",
                    title: "Draft findings",
                    lineage: { kind: "task", depth: 1 },
                  },
                ],
              },
            ],
            unresolvedTaskIds: ["hidden-task"],
            quarantinedTaskCount: 1,
          },
          observations: { available: true, observations: [] },
        }}
        loading={false}
        artifacts={{ evidence: null, proofPack: null }}
        events={[]}
        streamStatus="live"
        busyId=""
        controlReceipt={null}
        followUp=""
        followUpBusy={false}
        onFollowUpChange={vi.fn()}
        onSendFollowUp={vi.fn()}
        onArtifact={vi.fn()}
        onControl={vi.fn()}
        onResolveApproval={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Task tree" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Incomplete")).toBeInTheDocument();
    expect(screen.getByText("Review support evidence")).toBeInTheDocument();
    expect(screen.getByText(/Draft findings/)).toBeInTheDocument();
    expect(screen.queryByText("hidden-task")).not.toBeInTheDocument();
  });
});
