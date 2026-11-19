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
      error: mocks.detailError ?? null,
    }),
    useHasWorkers: () => mocks.hasWorkers ?? false,
    useCallWorkers: () => ({ data: undefined, isError: false }),
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

// The Participants panel asks for the transcript at a step, as the real panel's Show in transcript does.
vi.mock("../CallWorkersPanel", () => ({
  default: function WorkersStub({ onShowInTranscript }) {
    return (
      <button
        type="button"
        onClick={() => onShowInTranscript({ turn: 2, at: 12 })}
      >
        show step in transcript
      </button>
    );
  },
}));

const { default: ChatCallDrawer } = await import("../ChatCallDrawer");
const { STATUS_META } = await import("../../runs.constants");
const { TRIAL_CHANGES_COPY } = await import("../trialChanges.constants");

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

describe("ChatCallDrawer outcome reason", () => {
  it("says why a held trial was not graded, and nothing when there is no reason", () => {
    const reason =
      "Inconclusive, a problem of the scenario, not the agent: Rebuild the environment to enforce them.";
    const { rerender } = render(
      <ChatCallDrawer
        task={{ id: "call-1", status: "error", outcomeReason: reason }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(reason)).toBeInTheDocument();

    rerender(
      <ChatCallDrawer
        task={{ id: "call-1", status: "error" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText(reason)).not.toBeInTheDocument();
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
  it("shows no World state tab while actors are off", () => {
    mocks.callDetail = { actorsEnabled: false, trial_state: null };
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-14", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: "Transcript" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "World state" })).toBeNull();
    mocks.callDetail = null;
  });

  it("shows World state on a trial with no actors", async () => {
    mocks.callDetail = {
      actorsEnabled: true,
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
    expect(
      screen.getByLabelText("Committed: issue_refund"),
    ).toBeInTheDocument();
    mocks.callDetail = null;
  });

  it("says why World state were not recorded for an in-process agent", async () => {
    mocks.callDetail = {
      actorsEnabled: true,
      trial_state: {
        recorded: false,
        why: "in_process_tools",
        why_label:
          "World state is not recorded for this agent: its tools run inside it, where the test world cannot see them.",
      },
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
    mocks.callDetail = { actorsEnabled: true, trial_state: null };
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

  it.each(["queued", "in_progress"])(
    "says a %s call's World state shows once it ends, never that it was not recorded",
    async (status) => {
      mocks.callDetail = { trial_state: null, actorsEnabled: true };
      render(
        <ChatCallDrawer
          task={{ id: `call-execution-${status}`, status }}
          onClose={vi.fn()}
        />,
      );
      await userEvent.click(screen.getByRole("tab", { name: "World state" }));
      expect(
        screen.getByText(TRIAL_CHANGES_COPY.collecting),
      ).toBeInTheDocument();
      expect(screen.queryByText(TRIAL_CHANGES_COPY.notRecorded)).toBeNull();
      mocks.callDetail = null;
    },
  );

  it("shows no World state tab until the call's detail says actors are on", () => {
    mocks.callDetail = null;
    mocks.detailError = new Error("Request failed with status code 500");
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-13", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("tab", { name: "World state" })).toBeNull();
    mocks.detailError = null;
  });

  it("says nothing about World state while the call is still loading", async () => {
    mocks.loading = true;
    mocks.callDetail = { actorsEnabled: true };
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
    mocks.callDetail = null;
  });
});

describe("ChatCallDrawer, an earlier attempt", () => {
  it("never reads an attempt picked on one call against the next call's detail", async () => {
    const user = userEvent.setup();
    mocks.callDetail = {
      turns: [],
      stats: {},
      actorsEnabled: true,
      attemptNumber: 2,
      earlierAttempts: [
        {
          attempt: "a1",
          number: 1,
          rerun_at: "2026-11-17T09:00:00+00:00",
          kept: true,
          trial_state: null,
        },
      ],
    };
    const { rerender } = render(
      <ChatCallDrawer
        task={{ id: "call-1", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("combobox", { name: "Attempt" }));
    await user.click(
      screen.getByRole("option", { name: /Attempt 1, re-run at/ }),
    );
    await user.click(screen.getByRole("tab", { name: "World state" }));

    // Next: call 2, which was never re-run.
    mocks.callDetail = { turns: [], stats: {} };
    rerender(
      <ChatCallDrawer
        task={{ id: "call-2", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("combobox", { name: "Attempt" })).toBeNull();
    mocks.callDetail = null;
  });

  it("opens Participants for an earlier attempt even when the latest attempt kept no record", async () => {
    const user = userEvent.setup();
    mocks.callDetail = {
      turns: [],
      stats: {},
      actorsEnabled: true,
      attemptNumber: 2,
      earlierAttempts: [
        {
          attempt: "a1",
          number: 1,
          rerun_at: "2026-11-17T09:00:00+00:00",
          kept: true,
          trial_state: null,
        },
      ],
    };
    render(
      <ChatCallDrawer
        task={{ id: "call-1", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    // The latest attempt has no record: no Participants tab yet.
    expect(screen.queryByRole("tab", { name: "Participants" })).toBeNull();
    await user.click(screen.getByRole("combobox", { name: "Attempt" }));
    await user.click(
      screen.getByRole("option", { name: /Attempt 1, re-run at/ }),
    );
    await user.click(screen.getByRole("tab", { name: "Participants" }));
    expect(screen.getByRole("tab", { name: "Participants" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    mocks.callDetail = null;
  });
});

describe("ChatCallDrawer Participants", () => {
  it("Show in transcript from Participants opens the transcript on that turn", async () => {
    const user = userEvent.setup();
    Element.prototype.scrollIntoView = vi.fn();
    mocks.hasWorkers = true;
    mocks.callDetail = {
      turns: [
        { role: "customer", text: "First ask" },
        { role: "agent", text: "First reply" },
        { role: "customer", text: "Second ask" },
      ],
    };
    const { rerender } = render(
      <ChatCallDrawer
        task={{ id: "c1", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Participants" }));
    await user.click(
      screen.getByRole("button", { name: "show step in transcript" }),
    );
    expect(screen.getByRole("tab", { name: "Transcript" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByText("Second ask").closest("[data-focused]"),
    ).toHaveAttribute("data-focused", "true");
    // The next call's transcript opens unmarked.
    rerender(
      <ChatCallDrawer
        task={{ id: "c2", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    expect(document.querySelector('[data-focused="true"]')).toBeNull();
    mocks.hasWorkers = false;
    mocks.callDetail = null;
  });
});

describe("ChatCallDrawer step link", () => {
  it("a link to a Participants step opens on Participants", () => {
    mocks.hasWorkers = true;
    window.history.pushState({}, "", "/?participantsStep=2");
    render(
      <ChatCallDrawer
        task={{ id: "c1", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: "Participants" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    window.history.pushState({}, "", "/");
    mocks.hasWorkers = false;
  });
});

describe("ChatCallDrawer trial picker", () => {
  it("shows the scenario's trials in the header and opens the one picked", async () => {
    const user = userEvent.setup();
    const onPickTrial = vi.fn();
    render(
      <ChatCallDrawer
        task={{ id: "c2", status: "failed" }}
        trials={[
          { id: "c1", trialIndex: 1, status: "passed" },
          { id: "c2", trialIndex: 2, status: "failed" },
        ]}
        onPickTrial={onPickTrial}
        onClose={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Trial 1, Passed" }));
    expect(onPickTrial).toHaveBeenCalledWith("c1");
  });
});
