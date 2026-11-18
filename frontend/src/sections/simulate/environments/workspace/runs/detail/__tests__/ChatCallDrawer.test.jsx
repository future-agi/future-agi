/* eslint-disable react/prop-types */
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  runTraces: vi.fn(() => ({ projectId: null, traces: [], isError: false })),
  callDetail: null,
  loading: false,
}));

vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCallDetail: () => ({
      callDetail: mocks.callDetail,
      isLoading: mocks.loading,
    }),
    useCallTraces: (...args) => mocks.runTraces(...args),
  };
});

vi.mock("src/components/share-dialog", () => ({
  ShareDialog: ({ open, onClose, resourceType, resourceId }) => (
    <div
      data-testid="share-dialog"
      data-open={String(open)}
      data-resource-type={resourceType}
      data-resource-id={resourceId}
    >
      <button type="button" onClick={onClose}>
        close share
      </button>
    </div>
  ),
}));

const { default: ChatCallDrawer } = await import("../ChatCallDrawer");
const { STATUS_META } = await import("../../runs.constants");

describe("ChatCallDrawer share", () => {
  it("opens the share dialog for the call execution", async () => {
    const user = userEvent.setup();
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-1", status: "completed" }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("share-dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Share call" }));
    const dialog = screen.getByTestId("share-dialog");
    expect(dialog).toHaveAttribute("data-open", "true");
    expect(dialog).toHaveAttribute("data-resource-type", "call_execution");
    expect(dialog).toHaveAttribute("data-resource-id", "call-execution-1");

    await user.click(screen.getByRole("button", { name: "close share" }));
    expect(screen.queryByTestId("share-dialog")).not.toBeInTheDocument();
  });
});

describe("ChatCallDrawer tabs", () => {
  it("leaves sub-goal checks out of the Evals tab", () => {
    render(
      <ChatCallDrawer
        task={{
          id: "call-execution-2",
          status: "completed",
          evalResults: [
            {
              id: "ev-1",
              name: "Tone",
              kind: "evaluation",
              score: 1,
              passed: true,
            },
            {
              id: "sg-1",
              name: "pin_verified",
              kind: "sub_goal",
              score: 1,
              passed: true,
            },
          ],
        }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: "Evals (1)" })).toBeInTheDocument();
  });

  it("names a call's outcome instead of printing its raw status", async () => {
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-3", status: "no_score" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(STATUS_META.no_score.label)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Attributes" }));
    expect(screen.getAllByText(STATUS_META.no_score.label)).toHaveLength(2);
    expect(screen.queryByText("no_score")).not.toBeInTheDocument();
  });

  it("has no Checklist or Graph tab while they have no data behind them", () => {
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-1", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: "Transcript" })).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "Checklist" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "Graph" }),
    ).not.toBeInTheDocument();
  });
});

describe("ChatCallDrawer World state", () => {
  it("shows World state on a trial with no actors", async () => {
    mocks.callDetail = {
      trial_state: {
        state_changes: [
          {
            who: "agent",
            name: "the agent",
            attempted: 1,
            committed: 1,
            refused: 0,
            faulted: 0,
            duplicate: 0,
            committed_actions: ["issue_refund"],
          },
        ],
        duplicates: [],
        decisions: [],
        pre_run: [],
        approvals: [],
        usage: [],
        usage_summary: [],
        notices: [],
        unreadable: false,
      },
    };
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-9", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("tab", { name: "World state" }));
    expect(screen.getByText("Committed: issue_refund")).toBeInTheDocument();
    mocks.callDetail = null;
  });

  it("says why World state were not recorded for an in-process agent", async () => {
    mocks.callDetail = {
      trial_state: { recorded: false, why: "in_process_tools" },
    };
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-10", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("tab", { name: "World state" }));
    expect(
      screen.getByText(
        "World state is not recorded for this agent: its tools run inside it, where the test world cannot see them.",
      ),
    ).toBeInTheDocument();
    mocks.callDetail = null;
  });

  it("says World state were not recorded for an older trial with no record", async () => {
    mocks.callDetail = { trial_state: null };
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-11", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("tab", { name: "World state" }));
    expect(
      screen.getByText("World state was not recorded for this trial."),
    ).toBeInTheDocument();
    mocks.callDetail = null;
  });

  it("says nothing about World state while the call is still loading", async () => {
    mocks.loading = true;
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-12", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("tab", { name: "World state" }));
    expect(
      screen.queryByText("World state was not recorded for this trial."),
    ).toBeNull();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    mocks.loading = false;
  });
});
