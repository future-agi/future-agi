import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";

let result;
const asked = [];
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallWorkers: (...args) => {
    asked.push(args);
    return result;
  },
}));

const { default: CallWorkersPanel } = await import("../CallWorkersPanel");
const { CALL_WORKERS_COPY: C } = await import("../callTraces.constants");

import {
  handOffStep,
  oneTurn,
  counts,
  WORKERS,
  REQUEST,
  RESPONSE,
  DELEGATED,
  LOOKUP,
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
      expect(screen.getByTestId("supported-boundary")).toBeTruthy();
      expect(screen.queryByTestId("beyond-boundary") !== null).toBe(shown);
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
    expect(screen.getByText(note)).toBeInTheDocument();
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
    const call = LOOKUP;
    const tracedCall = () => ({
      data: {
        ...TIMELINE,
        steps: [
          {
            key: 9,
            kind: "tool_call",
            parent: null,
            depth: 0,
            worker_id: call.worker_id,
            call: { ...call, trace_id: "tr-1", span_id: "sp-1" },
            turn: 1,
            order: 1,
            relationship: "observed",
          },
        ],
        turns: oneTurn(REQUEST, RESPONSE, [9], 1),
        counts: counts(0, 0),
      },
      isLoading: false,
      isError: false,
    });
    it("offers no Open in Traces for a trace the Traces tab does not list", () => {
      result = tracedCall();
      render(
        <CallWorkersPanel
          callExecutionId="c1"
          onOpenTrace={vi.fn()}
          traceIds={["tr-other"]}
        />,
      );
      expect(screen.queryByRole("button", { name: C.openInTraces })).toBeNull();
    });

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
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    const [, handOff] = screen.getAllByTestId("raw-event");
    expect(handOff).not.toHaveTextContent("Returns coordinator");
    expect(handOff).toHaveTextContent(C.unknown);
  });
});

describe("CallWorkersPanel, an earlier attempt", () => {
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
