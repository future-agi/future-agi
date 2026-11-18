import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";

let result;
let routes;
const asked = [];
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallWorkers: (...args) => {
    asked.push(args);
    return result;
  },
  useTrialRoutes: () => ({ data: routes }),
}));

const { default: CallWorkersPanel } = await import("../CallWorkersPanel");
const { CALL_WORKERS_COPY: C } = await import("../callTraces.constants");

import {
  handOffStep,
  counts,
  WORKERS,
  DELEGATED,
  timeline,
} from "../participants/__fixtures__/workerRecord";

const TIMELINE = timeline();

describe("CallWorkersPanel", () => {
  beforeEach(() => {
    result = { data: TIMELINE, isLoading: false, isError: false };
  });

  it.each([false, true])(
    "says what multi-agent testing covers on every multi-agent call, and adds when the API says it went past it (%s)",
    (shown) => {
      result = {
        data: timeline({ beyond_boundary: shown }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      // Said in plain text under the summary, never only on hover.
      expect(screen.getByText(C.supportedBoundary)).toBeVisible();
      expect(screen.queryByText(C.beyondBoundary) !== null).toBe(shown);
    },
  );
  it("says when the record was built without the app's traces, and not otherwise", () => {
    const note = C.sandboxOnly;
    result = {
      data: { ...TIMELINE, spans_received: false },
      isLoading: false,
      isError: false,
    };
    const { unmount } = render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText(note)).toBeVisible();
    unmount();
    result = {
      data: { ...TIMELINE, spans_received: true },
      isLoading: false,
      isError: false,
    };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.queryByText(note)).toBeNull();
  });
  it("says why there is no record when the read failed or the attempt was kept before records were", () => {
    for (const state of ["unreadable", "not_kept"]) {
      result = {
        data: { ...timeline({ steps: [], turns: [] }), state },
        isLoading: false,
        isError: false,
      };
      const view = render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByText(C.states[state])).toBeInTheDocument();
      expect(screen.queryByTestId("worker-row")).toBeNull();
      view.unmount();
    }
  });
  it("shows a spinner until the record is read, and an error only with nothing to show", () => {
    result = { data: undefined, isLoading: true, isError: false };
    const { unmount } = render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    unmount();
    result = { data: undefined, isLoading: false, isError: true };
    const failed = render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText(C.error)).toBeInTheDocument();
    failed.unmount();
    // A failed refetch leaves what's already on screen.
    result = { data: TIMELINE, isLoading: false, isError: true };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(document.querySelector("[data-row]")).toBeInTheDocument();
    expect(screen.queryByText(C.error)).toBeNull();
  });

  describe("turns, placement and usage", () => {
    it("says a worker took part with no end recorded, never that it completed", () => {
      result = {
        data: {
          ...TIMELINE,
          workers: TIMELINE.workers.map((one) =>
            one.id === "order_lookup"
              ? { ...one, state: "observed_no_end" }
              : { ...one, state: "completed" },
          ),
        },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByTestId("workers-no-end")).toHaveTextContent(
        C.workersNoEnd("Order-lookup worker"),
      );
      expect(screen.getByTestId("workers-no-end")).not.toHaveTextContent(
        /completed/i,
      );
    });

    it("shows a worker's failed check on its hand-off, and a task check named by no worker and on no step", () => {
      result = {
        data: timeline({
          placements: [
            {
              name: "read_only",
              status: "failed",
              step: 1,
              worker_id: "r/order_lookup",
              untied: null,
            },
            {
              name: "explained_refusal",
              status: "inconclusive",
              step: null,
              worker_id: null,
              untied: "no_moment",
            },
          ],
        }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      const [own, task] = screen.getAllByTestId("placed-check");
      expect(own).toHaveTextContent("Order-lookup worker");
      expect(own.dataset.step).toBe("1");
      expect(own).not.toHaveTextContent(C.untied.no_moment);
      expect(task).not.toHaveTextContent("Order-lookup worker");
      expect(task.dataset.step).toBe("");
    });

    it("shows a worker's failed check failed and named by its worker, beside the call's passing verdict", () => {
      result = {
        data: timeline({
          workers: WORKERS.map((one) =>
            one.id === "order_lookup"
              ? { ...one, checks: [{ name: "read_only", passed: false }] }
              : one,
          ),
        }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      const [check] = screen.getAllByTestId("worker-check");
      expect(check.dataset.passed).toBe("false");
      expect(check).toHaveTextContent("Order-lookup worker");
      expect(check).toHaveTextContent(C.workerCheckResult(false));
    });

    it("says when nothing was recorded for the participants", () => {
      result = {
        data: {
          ...TIMELINE,
          events: [],
          steps: [],
          turns: [],
          collection: "no_evidence",
          workers: WORKERS.map((one) => ({ ...one, observed: false })),
          counts: counts(0, 0),
        },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByText(C.noEvidence)).toBeInTheDocument();
      // No empty step list under the note.
      expect(screen.queryByTestId("raw-events")).toBeNull();
    });
    it("says when only part of the call's traces could be read", () => {
      result = {
        data: { ...TIMELINE, collection: "partial" },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByText(C.partialCollection)).toBeInTheDocument();
    });

    it("says when the call's traces could not be read", () => {
      result = {
        data: { ...TIMELINE, collection: "unavailable" },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByText(C.unavailableCollection)).toBeInTheDocument();
    });
  });
  describe("as a sequence", () => {
    it("shows the sequence for a recorded call", () => {
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(document.querySelector('[data-row="2"]')).toBeInTheDocument();
    });

    it("lists a worker that never answered by its step and opens that step", () => {
      result = {
        data: timeline({ returned: null, end: "no_return" }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      const line = screen
        .getAllByTestId("participants-problem")
        .find((one) => one.textContent.includes("Step 2"));
      expect(line).toHaveTextContent("Step 2");
      expect(line).toHaveTextContent("Order-lookup worker didn't answer");
      fireEvent.click(line);
      expect(screen.getByTestId("step-detail")).toBeInTheDocument();
    });

    it.each(["not_kept", "unreadable"])(
      "a %s record shows its note and no diagram",
      (state) => {
        result = {
          data: timeline({ state }),
          isLoading: false,
          isError: false,
        };
        render(<CallWorkersPanel callExecutionId="c1" />);
        expect(screen.getByText(C.states[state])).toBeInTheDocument();
        expect(document.querySelector("[data-row]")).toBeNull();
      },
    );

    it("names declared workers with no events once, under the diagram", () => {
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(
        screen.getByText(/Declared, no events sent: Audit worker/),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(C.workersNotObserved("Audit worker")),
      ).toBeNull();
    });

    it("offers Open in trace only when the record names its trace project", () => {
      const traced = { ...DELEGATED, span_id: "s1", trace_id: "t1" };
      const steps = [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: traced,
          end: "no_return",
        }),
      ];
      const onOpenTrace = vi.fn();
      result = { data: timeline({ steps }), isLoading: false, isError: false };
      const { unmount } = render(
        <CallWorkersPanel callExecutionId="c1" onOpenTrace={onOpenTrace} />,
      );
      fireEvent.click(screen.getByTestId("participants-problem"));
      expect(
        screen.queryByRole("button", { name: /Open in trace/ }),
      ).toBeNull();
      unmount();
      result = {
        data: timeline({ steps, project_id: "p1" }),
        isLoading: false,
        isError: false,
      };
      render(
        <CallWorkersPanel callExecutionId="c1" onOpenTrace={onOpenTrace} />,
      );
      fireEvent.click(screen.getByTestId("participants-problem"));
      fireEvent.click(screen.getByRole("button", { name: /Open in trace/ }));
      expect(onOpenTrace).toHaveBeenCalledWith("t1", "s1");
    });
  });

  describe("links out of a step", () => {
    it("Show in transcript adds the gap between the record's start and the call's start, never below 0", () => {
      const onShowInTranscript = vi.fn();
      render(
        <CallWorkersPanel
          callExecutionId="c1"
          callStartedAt="2026-10-31T11:59:45+00:00"
          onShowInTranscript={onShowInTranscript}
        />,
      );
      fireEvent.click(document.querySelector('[data-row="2"]'));
      fireEvent.click(
        screen.getByRole("button", { name: /Show in transcript/ }),
      );
      // Record starts 11:59:50, the call at 11:59:45; the hand-off is 5.9 s into the record.
      expect(onShowInTranscript).toHaveBeenCalledWith({
        turn: 1,
        at: expect.closeTo(10.9, 3),
      });
    });

    it("a step link carries the API's step key, reopens that step once, and a refetch keeps the user's pick", () => {
      window.history.replaceState({}, "", "/run?participantsStep=1");
      const { rerender } = render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByTestId("step-detail")).toHaveTextContent(
        "inspect order 1042",
      );
      fireEvent.click(document.querySelector('[data-row="3"]'));
      result = { ...result, data: { ...TIMELINE } };
      rerender(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByTestId("step-detail")).toHaveTextContent("get_order");
      window.history.replaceState({}, "", "/");
    });
  });

  describe("a cut-short worker and a stopped call", () => {
    const cut = (cleanup) => ({
      ...WORKERS[0],
      state: "cancelled",
      cut_short: true,
      ...(cleanup ? { cleanup } : {}),
    });

    it.each([
      [{ status: "stopped_with_sandbox", at: "2026-10-31T12:01:00Z" }],
      [{ status: "still_running", at: null }],
      [{ status: "unknown", at: null }],
      [{ status: "stopped", at: "2026-10-31T12:01:00Z" }],
    ])("shows each cleanup status the API serves (%o)", (cleanup) => {
      result = {
        data: timeline({ workers: [cut(cleanup)] }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      const copy = C.cleanup[cleanup.status];
      expect(screen.getByTestId("worker-cleanup").textContent).toContain(
        typeof copy === "function" ? copy("").trim().replace(/,$/, "") : copy,
      );
    });

    it("shows no cleanup line on an old record", () => {
      result = {
        data: timeline({ workers: [cut(null)] }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.queryByTestId("worker-cleanup")).toBeNull();
    });

    it("says a call was stopped by the user only when the API says so", () => {
      result = {
        data: timeline({ stopped_by_user: true }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByTestId("stopped-by-user")).toBeInTheDocument();
      cleanup();
      result = { data: TIMELINE, isLoading: false, isError: false };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.queryByTestId("stopped-by-user")).toBeNull();
    });

    it("says a stopped call with no evidence was stopped before any participant's work, not that nothing was kept", () => {
      result = {
        data: timeline({
          collection: "no_evidence",
          events: [],
          steps: [],
          stopped_by_user: true,
        }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByText(C.stoppedByUserNoEvidence)).toBeInTheDocument();
      expect(screen.queryByTestId("stopped-by-user")).toBeNull();
      expect(screen.queryByText(C.noEvidence)).toBeNull();
    });
  });
});

describe("CallWorkersPanel, the record's own words", () => {
  it("names a worker the trial says reached the world on the shared address", () => {
    result = {
      data: timeline({
        workers: WORKERS.map((one) =>
          one.id === "order_lookup" ? { ...one, channel_bypassed: true } : one,
        ),
      }),
      isLoading: false,
      isError: false,
    };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByTestId("workers-bypassed")).toHaveTextContent(
      C.workersBypassed("Order-lookup worker"),
    );
  });

  it("says nothing of a bypass when no worker bypassed", () => {
    result = { data: TIMELINE, isLoading: false, isError: false };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.queryByTestId("workers-bypassed")).toBeNull();
  });

  it("never names the coordinator as the maker of a hand-off the record cannot place", () => {
    const unplaced = timeline();
    unplaced.steps = unplaced.steps.map((one) =>
      one.kind === "handoff"
        ? {
            ...one,
            relationship: "unknown",
            handoff: { ...one.handoff, by: null },
          }
        : one,
    );
    result = { data: unplaced, isLoading: false, isError: false };
    render(<CallWorkersPanel callExecutionId="c1" />);
    // The unplaced hand-off comes from the "not tied to a participant" lane, never the coordinator's.
    fireEvent.click(
      screen.getByRole("button", { name: "Follow Not tied to a participant" }),
    );
    expect(
      screen.getByText(/^Following Not tied to a participant · 1 of/),
    ).toBeInTheDocument();
  });
});

describe("CallWorkersPanel, an earlier attempt", () => {
  it("ignores a step link and offers no transcript, trace or link out of an earlier attempt's steps", () => {
    window.history.replaceState({}, "", "/run?participantsStep=1");
    result = {
      data: { ...TIMELINE, project_id: "p1" },
      isLoading: false,
      isError: false,
    };
    render(
      <CallWorkersPanel
        callExecutionId="c1"
        attempt="a1"
        onOpenTrace={vi.fn()}
        onShowInTranscript={vi.fn()}
      />,
    );
    // The link names a step of the latest attempt's record, not this one's.
    expect(screen.queryByTestId("step-detail")).toBeNull();
    fireEvent.click(document.querySelector('[data-row="2"]'));
    expect(screen.getByTestId("step-detail")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Show in transcript/ }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /Open in trace/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Copy a link/ })).toBeNull();
    window.history.replaceState({}, "", "/");
  });

  it("reads the earlier attempt's record when one is picked, and the call's own otherwise", () => {
    result = { data: TIMELINE, isLoading: false, isError: false };
    asked.length = 0;
    render(
      <CallWorkersPanel
        callExecutionId="c1"
        callStatus="completed"
        attempt="a1"
      />,
    );
    expect(asked.at(-1)).toEqual(["c1", "completed", "a1"]);
    render(<CallWorkersPanel callExecutionId="c1" callStatus="completed" />);
    expect(asked.at(-1)).toEqual(["c1", "completed", null]);
  });
});

describe("CallWorkersPanel, the scenario's trials", () => {
  // Both trials asked the order-lookup worker; the API keys that hand-off as step 1 in each.
  const asked1 = { state: "step", step: 1, steps: [1], count: 1 };
  const twoTrials = {
    scenario_key: "refund",
    tool_calls: false,
    columns: [
      {
        key: "handoff:coordinator:>worker:order_lookup",
        kind: "handoff",
        from_kind: "coordinator",
        from_id: null,
        from_name: "Returns coordinator",
        to_kind: "worker",
        to_id: "order_lookup",
        to_name: "Order-lookup worker",
        tool: null,
      },
    ],
    trials: [
      {
        number: 1,
        call_execution_id: "c1",
        result: "passed",
        route: "recorded",
        cells: [asked1],
      },
      {
        number: 2,
        call_execution_id: "c2",
        result: "failed",
        route: "recorded",
        cells: [asked1],
      },
    ],
    split: null,
    same_route: true,
  };

  it("a cell picks the step on this call, and opens another trial at its step", () => {
    result = { data: timeline(), isLoading: false, isError: false };
    routes = twoTrials;
    const onOpenTrial = vi.fn();
    render(
      <CallWorkersPanel
        callExecutionId="c1"
        testExecutionId="ex1"
        scenarioKey="refund"
        onOpenTrial={onOpenTrial}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show trials" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Open trial 1 at this step" }),
    );
    expect(screen.getByTestId("step-detail")).toHaveTextContent(
      "inspect order 1042",
    );
    expect(onOpenTrial).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Open trial 2 at this step" }),
    );
    expect(onOpenTrial).toHaveBeenCalledWith("c2", 1);
    routes = undefined;
  });
});

describe("CallWorkersPanel, a record not read yet or lost", () => {
  it("says an earlier attempt with no hand-off had none, instead of loading for ever", () => {
    result = { data: null, isLoading: false, isError: false };
    render(<CallWorkersPanel callExecutionId="c1" attempt="a1" />);
    expect(screen.getByText(C.noHandOffInAttempt)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("says a stopped call's participants are still being collected, instead of hiding them", async () => {
    const { COLLECTING_WORKERS } = await import(
      "src/api/simulate-environments/runDetail"
    );
    result = { data: COLLECTING_WORKERS, isLoading: false, isError: false };
    render(<CallWorkersPanel callExecutionId="c1" callStatus="cancelled" />);
    expect(
      screen.getByText("Collecting this call's participants…"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("still waits while the latest attempt's record is unread", () => {
    result = { data: null, isLoading: false, isError: false };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("says an attempt lost to an infrastructure failure was lost, never that the app sent nothing", () => {
    result = {
      data: {
        ...TIMELINE,
        events: [],
        steps: [],
        turns: [],
        collection: "infrastructure_lost",
        workers: WORKERS.map((one) => ({
          ...one,
          observed: false,
          state: "unknown",
        })),
        counts: counts(0, 0),
      },
      isLoading: false,
      isError: false,
    };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByTestId("infrastructure-lost")).toHaveTextContent(
      C.infrastructureLost,
    );
    expect(screen.queryByText(C.noEvidence)).toBeNull();
    // Its workers are not known, never "Not observed".
    expect(screen.getByTestId("workers-unknown")).toHaveTextContent(
      "Not known: the attempt was lost to an infrastructure failure",
    );
    expect(screen.queryByText(/^Not observed:/)).toBeNull();
  });
});
