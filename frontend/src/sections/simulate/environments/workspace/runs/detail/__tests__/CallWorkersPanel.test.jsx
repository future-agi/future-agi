import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";

import { withSteps } from "./workerSteps.fixture";

let result;
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallWorkers: () => result && { ...result, data: withSteps(result.data) },
}));

const { default: CallWorkersPanel } = await import("../CallWorkersPanel");

// A call with no hand-off shows its steps with no toggle.
const renderSteps = (ui) => {
  const view = render(ui);
  const toggle = screen.queryByRole("checkbox", { name: "Raw events" });
  if (toggle) fireEvent.click(toggle);
  return view;
};

const TIMELINE = {
  collected_at: "2026-10-31T12:00:00Z",
  started_at: "2026-10-31T11:59:50",
  coordinator: { name: "Returns coordinator" },
  request: { text: "Please refund order 1042", at: "2026-10-31T11:59:54" },
  response: { text: "Your refund is on its way", at: "2026-10-31T12:00:10" },
  workers: [
    {
      worker_id: "r/order_lookup",
      id: "order_lookup",
      name: "Order-lookup worker",
      observed: true,
      undeclared: false,
      instance_ids: ["s3"],
    },
    {
      worker_id: "r/audit",
      id: "audit",
      name: "Audit worker",
      observed: false,
      undeclared: false,
      instance_ids: [],
    },
  ],
  events: [
    {
      seq: 1,
      type: "delegated",
      worker_id: "r/order_lookup",
      task: "inspect order 1042",
      at: "2026-10-31T11:59:55.900",
      attribution: "reported",
    },
    {
      seq: 2,
      type: "tool_call",
      worker_id: "r/order_lookup",
      tool: "get_order",
      status: "error",
      input: { order_id: "1042" },
      output: "store unavailable",
      at: "2026-10-31T11:59:57",
      attribution: "reported",
    },
    {
      seq: 3,
      type: "returned",
      worker_id: "r/order_lookup",
      result: "eligible",
      at: "2026-10-31T11:59:59",
      attribution: "reported",
    },
    {
      seq: 4,
      type: "tool_call",
      worker_id: null,
      tool: "send_reply",
      status: "ok",
      at: "2026-10-31T12:00:09",
      attribution: "reported",
    },
  ],
};

describe("CallWorkersPanel", () => {
  beforeEach(() => {
    result = { data: TIMELINE, isLoading: false, isError: false };
  });

  const steps = () =>
    screen.getAllByTestId("raw-event").map((one) => one.dataset.type);

  it("lists every step in order, from the request through each hand-off and its calls to the reply", () => {
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(steps()).toEqual([
      "Message",
      "Hand-off",
      "Tool call",
      "Answer",
      "Tool call",
      "Reply",
    ]);
    const [, handOff, tool] = screen.getAllByTestId("raw-event");
    expect(handOff).toHaveTextContent("Returns coordinator");
    expect(handOff).toHaveTextContent("→ Order-lookup worker");
    expect(handOff).toHaveTextContent("inspect order 1042");
    expect(tool).toHaveTextContent("Order-lookup worker");
    expect(tool).toHaveTextContent("get_order");
    expect(tool).toHaveTextContent("error");
  });

  it("says when the record was built without the app's traces, and not otherwise", () => {
    const note = /the app sent no traces, so the Traces tab is empty/;
    result = {
      data: { ...TIMELINE, spans_received: false },
      isLoading: false,
      isError: false,
    };
    const { unmount } = renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText(note)).toBeInTheDocument();
    unmount();
    result = {
      data: { ...TIMELINE, spans_received: true },
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.queryByText(note)).toBeNull();
  });

  it("shows an answer returned as data as code, kept as it came", () => {
    result = {
      data: {
        ...TIMELINE,
        events: TIMELINE.events.map((one) =>
          one.type === "returned"
            ? { ...one, result: "{'valid': False, 'feedback': 'Too_short'}" }
            : one,
        ),
      },
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    const [returned] = screen
      .getAllByTestId("answer")
      .filter((one) => one.textContent.includes("feedback"));
    expect(returned.querySelector("pre")).toHaveTextContent(
      "{'valid': False, 'feedback': 'Too_short'}",
    );
  });

  it("folds a long answer to a few lines until asked for the rest", () => {
    const answer = Array.from(
      { length: 12 },
      (_, index) => `line ${index + 1}`,
    ).join("\n\n");
    result = {
      data: {
        ...TIMELINE,
        events: TIMELINE.events.map((one) =>
          one.type === "returned" ? { ...one, result: answer } : one,
        ),
      },
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    const [returned] = screen
      .getAllByTestId("answer")
      .filter((one) => one.textContent.includes("line 12"));
    expect(returned).toHaveAttribute("data-clamped", "true");
    // The short request and reply are shown whole, with nothing to open.
    expect(screen.getAllByText("Show more")).toHaveLength(1);
    fireEvent.click(screen.getByText("Show more"));
    expect(returned).toHaveAttribute("data-clamped", "false");
    fireEvent.click(screen.getByText("Show less"));
    expect(returned).toHaveAttribute("data-clamped", "true");
  });

  it("marks declared workers that sent nothing, with no request or reply when nothing was handed out", () => {
    result = {
      data: {
        ...TIMELINE,
        workers: TIMELINE.workers.slice(1),
        events: [TIMELINE.events[3]],
      },
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("Not observed: Audit worker")).toBeInTheDocument();
    expect(steps()).toEqual(["Tool call"]);
  });

  it("times each step from the call's start", () => {
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("0:04")).toBeInTheDocument();
    expect(screen.getByText("0:05")).toBeInTheDocument();
    expect(screen.getByText("0:07")).toBeInTheDocument();
    expect(screen.getByText("0:20")).toBeInTheDocument();
  });

  it("shows a tool call's input and output until its JSON is hidden", () => {
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("store unavailable")).toBeInTheDocument();
    const [button] = screen.getAllByRole("button", { name: "Hide JSON" });
    fireEvent.click(button);
    expect(screen.getByText("store unavailable")).not.toBeVisible();
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("says what is missing for a request, reply, coordinator or return that was not seen", () => {
    result = {
      data: {
        ...TIMELINE,
        coordinator: null,
        request: null,
        response: undefined,
        events: TIMELINE.events.filter((one) => one.type !== "returned"),
      },
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getAllByText("Coordinator").length).toBeGreaterThan(0);
    expect(screen.getByText("No request recorded")).toBeInTheDocument();
    expect(screen.getByText("No reply recorded")).toBeInTheDocument();
    expect(
      screen.getByText(
        "No return from Order-lookup worker before the call ended",
      ),
    ).toBeInTheDocument();
  });

  it("shows what a worker returned as formatted text, not raw markdown", () => {
    result = {
      data: {
        ...TIMELINE,
        events: [
          {
            seq: 1,
            type: "returned",
            worker_id: "r/order_lookup",
            result: "**Order 1042**\n- Delivered\n- Inside the window",
            at: "t1",
            attribution: "reported",
          },
        ],
      },
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("Order 1042").tagName).toBe("STRONG");
    expect(
      screen.getAllByRole("listitem").map((item) => item.textContent),
    ).toEqual(["Delivered", "Inside the window"]);
    expect(screen.queryByText(/\*\*/)).toBeNull();
  });

  it("doesn't load an image a worker's text points at", () => {
    result = {
      data: {
        ...TIMELINE,
        events: TIMELINE.events.map((one) =>
          one.type === "returned"
            ? { ...one, result: "Done ![](https://host/x?d=secret)" }
            : one,
        ),
      },
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("says a call no agent can be named for is unknown, not the coordinator's", () => {
    result = {
      data: {
        ...TIMELINE,
        events: [
          {
            seq: 1,
            type: "tool_call",
            worker_id: null,
            tool: "get_order",
            status: "ok",
            at: "t1",
            attribution: "unattributed",
          },
        ],
      },
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByTestId("raw-event")).toHaveTextContent("Unknown");
    expect(screen.getByTestId("raw-event")).not.toHaveTextContent(
      "Coordinator",
    );
  });

  it("shows a spinner until the record is read, and an error only with nothing to show", () => {
    result = { data: undefined, isLoading: true, isError: false };
    const { unmount } = renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    unmount();
    result = { data: undefined, isLoading: false, isError: true };
    const failed = renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByText("Couldn't load this call's participants."),
    ).toBeInTheDocument();
    failed.unmount();
    // A failed refetch leaves what's already on screen.
    result = { data: TIMELINE, isLoading: false, isError: true };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByTestId("raw-events")).toBeInTheDocument();
    expect(
      screen.queryByText("Couldn't load this call's participants."),
    ).toBeNull();
  });

  describe("steps", () => {
    const worker = (id, observed = true) => ({
      worker_id: `r/${id}`,
      id,
      name: `${id} worker`,
      observed,
      undeclared: false,
      instance_ids: [],
    });
    const at = (second) =>
      `2026-10-31T12:00:${String(second).padStart(2, "0")}`;
    const timeline = (workers, events) => ({
      data: {
        ...TIMELINE,
        workers,
        events: events.map((one, index) => ({
          attribution: "reported",
          ...one,
          seq: index + 1,
        })),
      },
      isLoading: false,
      isError: false,
    });

    it("credits a nested hand-off to the agent that made it, and keeps a control pass with no answer", () => {
      result = timeline(
        [worker("inspiration"), worker("place")],
        [
          {
            type: "transferred",
            worker_id: "r/inspiration",
            by: null,
            at: at(1),
          },
          {
            type: "delegated",
            worker_id: "r/place",
            by: "r/inspiration",
            task: "find places",
            at: at(2),
          },
          {
            type: "returned",
            worker_id: "r/place",
            result: "places",
            at: at(5),
          },
        ],
      );
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(steps()).toEqual([
        "Message",
        "Control pass",
        "Hand-off",
        "Answer",
        "Reply",
      ]);
      const [, pass, handOff] = screen.getAllByTestId("raw-event");
      expect(pass).toHaveTextContent("→ inspiration worker");
      expect(handOff).toHaveTextContent("inspiration worker");
      expect(handOff).toHaveTextContent("→ place worker");
    });

    it("marks sibling hand-offs whose spans overlap as in parallel", () => {
      result = timeline(
        [worker("research"), worker("code"), worker("review")],
        [
          {
            type: "delegated",
            worker_id: "r/research",
            task: "read",
            at: at(1),
          },
          { type: "delegated", worker_id: "r/code", task: "write", at: at(2) },
          {
            type: "returned",
            worker_id: "r/research",
            result: "notes",
            at: at(5),
          },
          { type: "returned", worker_id: "r/code", result: "patch", at: at(6) },
          {
            type: "delegated",
            worker_id: "r/review",
            task: "check",
            at: at(7),
          },
          { type: "returned", worker_id: "r/review", result: "ok", at: at(8) },
        ],
      );
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getAllByText("in parallel")).toHaveLength(2);
    });

    it("says why a step's payload, later steps or a return are missing when the record reached its limit", () => {
      result = timeline(
        [worker("a"), worker("b")],
        [
          { type: "delegated", worker_id: "r/b", task: "two", at: at(1) },
          { type: "delegated", worker_id: "r/a", task: "one", at: at(1) },
          {
            type: "tool_call",
            worker_id: "r/a",
            tool: "lookup",
            status: "ok",
            input: null,
            output: null,
            payload_omitted: true,
            at: at(2),
          },
          { type: "returned", worker_id: "r/a", result: "x", at: at(3) },
        ],
      );
      const uncut = result;
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByText(/^No return from b worker/)).toBeInTheDocument();
      cleanup();
      result = {
        ...uncut,
        data: { ...uncut.data, events_omitted: 12 },
      };
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(
        screen.getByText(/^Return from b worker not shown/),
      ).toBeInTheDocument();
      expect(screen.queryByText(/^No return from/)).toBeNull();
      expect(
        screen.getAllByText(
          "Not kept: this call's record reached its size limit",
        ),
      ).toHaveLength(2);
      expect(
        screen.getByText(
          "12 more steps not shown: this call's record reached its size limit",
        ),
      ).toBeInTheDocument();
    });

    it("keeps a long record's JSON closed, so it stays quick to draw", () => {
      result = timeline(
        [worker("a")],
        [
          { type: "delegated", worker_id: "r/a", task: "many", at: at(1) },
          ...Array.from({ length: 21 }, (_, index) => ({
            type: "tool_call",
            worker_id: "r/a",
            tool: `tool_${index}`,
            status: "ok",
            input: { n: index },
            at: at(2),
          })),
        ],
      );
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.queryByRole("button", { name: "Hide JSON" })).toBeNull();
      expect(screen.getAllByRole("button", { name: "Show JSON" })).toHaveLength(
        21,
      );
    });
  });

  describe("turns, placement and usage", () => {
    const handoff = TIMELINE.events[0];
    const call = TIMELINE.events[1];
    const reply = TIMELINE.events[2];
    const step = (key, turn, order, extra) => ({
      key,
      kind: "handoff",
      parent: null,
      depth: 1,
      worker_id: handoff.worker_id,
      transfer: false,
      handoff: { ...handoff, seq: key, task: `task ${key}` },
      returned: { ...reply, seq: key + 100 },
      parallel: false,
      turn,
      order,
      relationship: "observed",
      ...extra,
    });
    const twoTurns = (extra = {}) => ({
      ...TIMELINE,
      steps: [step(1, 1, 1), step(2, 2, 1, extra)],
      turns: [
        {
          turn: 1,
          request: { text: "first ask", at: "t1" },
          response: { text: "first reply", at: "t2" },
        },
        {
          turn: 2,
          request: { text: "second ask", at: "t3" },
          response: { text: "second reply", at: "t4" },
        },
      ],
    });

    it("gives each turn its own request, hand-off table and reply", () => {
      result = { data: twoTurns(), isLoading: false, isError: false };
      render(<CallWorkersPanel callExecutionId="c1" />);
      const turns = screen.getAllByTestId("turn");
      expect(turns).toHaveLength(2);
      expect(turns[0]).toHaveTextContent("Turn 1 · Caller's request");
      expect(turns[0]).toHaveTextContent("first ask");
      expect(turns[0]).toHaveTextContent("task 1");
      expect(turns[0]).not.toHaveTextContent("task 2");
      expect(turns[1]).toHaveTextContent("second ask");
      expect(turns[1]).toHaveTextContent("task 2");
      expect(turns[1]).toHaveTextContent("second reply");
    });

    it("marks a hand-off the record can't place as parent unknown, and only that one", () => {
      result = {
        data: twoTurns({ relationship: "unknown" }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getAllByTestId("parent-unknown")).toHaveLength(1);
      expect(screen.getAllByTestId("turn")[1]).toContainElement(
        screen.getByTestId("parent-unknown"),
      );
    });

    it("shows a worker's model and tokens under its name when the app sent them", () => {
      result = {
        data: {
          ...twoTurns(),
          workers: TIMELINE.workers.map((one) =>
            one.worker_id === handoff.worker_id
              ? { ...one, model: "worker-model", tokens: 1200 }
              : one,
          ),
        },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getAllByText("worker-model · 1,200 tokens")).toHaveLength(
        2,
      );
    });

    it("opens a step's trace in Traces when the step was read from one", () => {
      const onOpenTrace = vi.fn();
      result = {
        data: {
          ...TIMELINE,
          steps: [
            {
              key: 9,
              kind: "tool_call",
              parent: null,
              depth: 0,
              worker_id: call.worker_id,
              call: { ...call, trace_id: "tr-1" },
              turn: 1,
              order: 1,
              relationship: "observed",
            },
          ],
          turns: [
            { turn: 1, request: TIMELINE.request, response: TIMELINE.response },
          ],
        },
        isLoading: false,
        isError: false,
      };
      render(
        <CallWorkersPanel callExecutionId="c1" onOpenTrace={onOpenTrace} />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Open in Traces" }));
      expect(onOpenTrace).toHaveBeenCalledWith("tr-1");
    });

    it("says when nothing was recorded for the participants", () => {
      result = {
        data: {
          ...TIMELINE,
          events: [],
          steps: [],
          turns: [],
          collection: "no_evidence",
          workers: TIMELINE.workers.map((one) => ({ ...one, observed: false })),
        },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(
        screen.getByText(/Nothing was recorded for this call's participants/),
      ).toBeInTheDocument();
    });
  });

  describe("as a table", () => {
    const worker = (id, name, observed = true) => ({
      worker_id: id,
      id,
      name,
      observed,
      undeclared: false,
      instance_ids: [],
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
      at: "2026-10-31T11:59:56",
      attribution: "reported",
    });
    const returned = (seq, workerId, delegation, text, status = "ok") => ({
      seq,
      type: "returned",
      worker_id: workerId,
      delegation_id: delegation,
      result: text,
      status,
      at: "2026-10-31T11:59:58",
      attribution: "reported",
    });
    const TABLE = {
      ...TIMELINE,
      workers: [
        worker("r/lookup", "Lookup"),
        worker("r/refund", "Refund"),
        worker("r/audit", "Audit", false),
      ],
      events: [
        handOff(1, "r/lookup", "find order 1042"),
        tool(2, "r/lookup", "get_order"),
        returned(3, "r/lookup", "d1", "Order 1042 is eligible"),
        handOff(4, "r/refund", "refund order 1042"),
        tool(5, "r/refund", "issue_refund", {
          status: "error",
          output: "gateway returned 503",
        }),
        returned(6, "r/refund", "d4", null, "error"),
      ],
    };
    const row = (name) =>
      screen
        .getAllByTestId("worker-row")
        .find((one) => one.dataset.worker === name);

    beforeEach(() => {
      result = { data: TABLE, isLoading: false, isError: false };
    });

    it("opens the table with a count of how hand-offs ended and marks the failed one", () => {
      render(<CallWorkersPanel callExecutionId="c1" />);

      expect(
        screen.getByText("2 hand-offs to 2 workers. 1 finished, 1 didn't."),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("raw-events")).toBeNull();
      expect(row("Lookup")).toHaveAttribute("data-end", "finished");
      expect(row("Refund")).toHaveAttribute("data-end", "failed");
      // The icon alone can't say so: the button's own label decides what a screen reader hears.
      expect(
        screen.getByRole("button", {
          name: "Show raw data for Refund, error",
        }),
      ).toBeInTheDocument();
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
      fireEvent.click(
        screen.getByRole("button", { name: "Show raw data for Lookup" }),
      );

      const raw = screen.getByTestId("worker-row-raw");
      expect(raw).toHaveTextContent("get_order");
      expect(raw).not.toHaveTextContent("issue_refund");

      fireEvent.click(screen.getByRole("checkbox", { name: "Raw events" }));
      expect(steps()).toContain("Reply");
      expect(screen.queryByTestId("worker-row")).toBeNull();
    });
  });
});
