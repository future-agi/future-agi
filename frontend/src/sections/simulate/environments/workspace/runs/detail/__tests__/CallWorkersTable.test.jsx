import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import {
  handOffStep,
  callStep,
  oneTurn,
  prose,
  counts,
  REQUEST,
  RESPONSE,
  timeline,
} from "./callWorkersFixtures";

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

// A call with no hand-off shows its steps with no toggle.
const renderSteps = (ui) => {
  const view = render(ui);
  const toggle = screen.queryByRole("checkbox", { name: C.rawEvents });
  if (toggle) fireEvent.click(toggle);
  return view;
};

describe("CallWorkersTable", () => {
  const steps = () =>
    screen.getAllByTestId("raw-event").map((one) => one.dataset.type);

  const worker = (id, name, observed = true) => ({
    worker_id: id,
    id,
    name,
    observed,
    undeclared: false,
    instance_ids: [],
    cut_short: false,
    checks: [],
  });
  const tool = (seq, workerId, name, extra) => ({
    seq,
    type: "tool_call",
    worker_id: workerId,
    tool: name,
    status: "ok",
    input: { order_id: "1042" },
    output: { ok: true },
    at: "2026-10-31T11:59:57",
    attribution: "reported",
    ...extra,
  });
  const handOff = (seq, workerId, task, by) => ({
    seq,
    type: "delegated",
    worker_id: workerId,
    by,
    delegation_id: `d${seq}`,
    task,
    task_shown: task ? prose(task) : null,
    at: "2026-10-31T11:59:56",
    attribution: "reported",
  });
  const returned = (seq, workerId, delegation, text, status = "ok") => ({
    seq,
    type: "returned",
    worker_id: workerId,
    delegation_id: delegation,
    result: text,
    result_shown: text ? prose(text) : null,
    status,
    at: "2026-10-31T11:59:58",
    attribution: "reported",
  });
  const EVENTS = [
    handOff(1, "r/lookup", "find order 1042"),
    tool(2, "r/lookup", "get_order", { delegation_id: "d1" }),
    returned(3, "r/lookup", "d1", "Order 1042 is eligible"),
    handOff(4, "r/refund", "refund order 1042"),
    tool(5, "r/refund", "issue_refund", {
      delegation_id: "d4",
      status: "error",
      output: "gateway returned 503",
    }),
    returned(6, "r/refund", "d4", null, "error"),
  ];
  const TABLE = timeline({
    workers: [
      worker("r/lookup", "Lookup"),
      worker("r/refund", "Refund"),
      worker("r/audit", "Audit", false),
    ],
    events: EVENTS,
    steps: [
      handOffStep(1, {
        worker_id: "r/lookup",
        handoff: EVENTS[0],
        returned: EVENTS[2],
        end: "finished",
        children: [2],
      }),
      callStep(EVENTS[1], { parent: 1, depth: 1 }),
      handOffStep(4, {
        worker_id: "r/refund",
        handoff: EVENTS[3],
        returned: EVENTS[5],
        end: "failed",
        children: [5],
      }),
      callStep(EVENTS[4], { parent: 4, depth: 1 }),
    ],
    turns: oneTurn(REQUEST, RESPONSE, [1, 4]),
    counts: counts(1, 1, { workers: 2 }),
  });
  const row = (name) =>
    screen
      .getAllByTestId("worker-row")
      .find((one) => one.dataset.worker === name);

  beforeEach(() => {
    result = { data: TABLE, isLoading: false, isError: false };
  });

  it("opens the table with the API's count of how hand-offs ended and marks the failed one", () => {
    const view = render(<CallWorkersPanel callExecutionId="c1" />);
    const summary = C.handOffSummary(TABLE.counts);
    expect(screen.getByText(summary)).toBeInTheDocument();
    expect(screen.queryByTestId("raw-events")).toBeNull();
    // The headline follows the counts the API sends.
    result = {
      data: { ...TABLE, counts: counts(2, 0, { workers: 2 }) },
      isLoading: false,
      isError: false,
    };
    view.rerender(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.queryByText(summary)).toBeNull();
    expect(row("Lookup")).toHaveAttribute("data-end", "finished");
    expect(row("Refund")).toHaveAttribute("data-end", "failed");
    // The icon alone can't say so: the button's own label decides what a screen reader hears.
    expect(
      screen.getByRole("button", {
        name: `${C.showRow("Refund")}, ${C.failed}`,
      }),
    ).toBeInTheDocument();
  });

  it("marks a row failed by the API's did_not_finish, whatever its end", () => {
    result = {
      data: {
        ...TABLE,
        steps: TABLE.steps.map((step) =>
          step.kind === "handoff"
            ? { ...step, did_not_finish: step.key === 1 }
            : step,
        ),
      },
      isLoading: false,
      isError: false,
    };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByRole("button", {
        name: `${C.showRow("Lookup")}, ${C.failed}`,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: C.showRow("Refund") }),
    ).toBeInTheDocument();
  });

  it("never draws a hand-off a user stop cut short as an error", () => {
    for (const endState of ["cancelled", "cancel_requested"]) {
      result = {
        data: {
          ...TABLE,
          steps: TABLE.steps.map((step) =>
            step.key === 4
              ? {
                  ...step,
                  end: "no_return",
                  end_state: endState,
                  did_not_finish: true,
                  returned: null,
                }
              : step,
          ),
        },
        isLoading: false,
        isError: false,
      };
      const { unmount } = render(<CallWorkersPanel callExecutionId="c1" />);
      // No ", error" in its name, and so no red icon or red answer beside it.
      expect(
        screen.getByRole("button", { name: C.showRow("Refund") }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", {
          name: `${C.showRow("Refund")}, ${C.failed}`,
        }),
      ).toBeNull();
      unmount();
    }
  });

  it("shows an answer the API marks as data as code in its table cell and in the raw events", () => {
    const json = '{\n  "refund_id": "RF-8"\n}';
    const data = {
      ...TABLE,
      steps: TABLE.steps.map((step) =>
        step.key === 1
          ? {
              ...step,
              returned: {
                ...step.returned,
                result_shown: { format: "code", text: json },
              },
            }
          : step,
      ),
    };
    result = { data, isLoading: false, isError: false };
    const view = render(<CallWorkersPanel callExecutionId="c1" />);
    expect(row("Lookup").querySelector("code")).toHaveTextContent(
      '"refund_id": "RF-8"',
    );
    view.unmount();
    result = { data, isLoading: false, isError: false };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    const [answer] = screen
      .getAllByTestId("answer")
      .filter((one) => one.textContent.includes("RF-8"));
    expect(answer.querySelector("pre")).toHaveTextContent('"refund_id"');
  });

  it("leaves a row as it is when the click ends a text selection, but not for a keyboard press", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    const selection = vi
      .spyOn(window, "getSelection")
      .mockReturnValue({ toString: () => "copied text" });

    fireEvent.click(row("Lookup"), { detail: 1 });
    expect(screen.queryByTestId("worker-row-raw")).toBeNull();

    fireEvent.click(row("Lookup"), { detail: 0 });
    expect(screen.getByTestId("worker-row-raw")).toBeInTheDocument();
    selection.mockRestore();
  });

  it("opens a failed tool with its row, so the error is one click from the table", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.queryByText("gateway returned 503")).toBeNull();

    fireEvent.click(row("Refund"));
    expect(screen.getByText("gateway returned 503")).toBeInTheDocument();
  });

  it("opens only the clicked row's raw data, and the toggle swaps the table for every step", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: C.showRow("Lookup") }));

    const raw = screen.getByTestId("worker-row-raw");
    expect(raw).toHaveTextContent("get_order");
    expect(raw).not.toHaveTextContent("issue_refund");

    fireEvent.click(screen.getByRole("checkbox", { name: C.rawEvents }));
    expect(steps()).toContain(C.replyStep);
    expect(screen.queryByTestId("worker-row")).toBeNull();
  });
});
