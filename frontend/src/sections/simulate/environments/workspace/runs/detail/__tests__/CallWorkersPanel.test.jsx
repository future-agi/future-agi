import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";

let result;
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallWorkers: () => result,
}));

const { default: CallWorkersPanel } = await import("../CallWorkersPanel");

// A call with no hand-off shows its steps with no toggle.
const renderSteps = (ui) => {
  const view = render(ui);
  const toggle = screen.queryByRole("checkbox", { name: "Raw events" });
  if (toggle) fireEvent.click(toggle);
  return view;
};

// The API's rows, written as it sends them: simulate/services/harness_worker_steps.py arranges a call's events
// into these, and is tested there.
const handOffStep = (
  key,
  {
    parent = null,
    depth = 1,
    worker_id,
    handoff = null,
    returned = null,
    parallel = false,
    relationship = "observed",
  },
) => ({
  kind: "handoff",
  key,
  parent,
  depth,
  worker_id,
  transfer: handoff?.type === "transferred",
  handoff,
  returned,
  parallel,
  turn: 1,
  order: key,
  relationship,
});
const callStep = (call, { parent = null, depth = 0 } = {}) => ({
  kind: "tool_call",
  key: call.seq,
  parent,
  depth,
  worker_id: call.worker_id,
  call,
  turn: 1,
  order: call.seq,
  relationship: "observed",
});
const oneTurn = (request, response) => [{ turn: 1, request, response }];

const WORKERS = [
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
];
const REQUEST = { text: "Please refund order 1042", at: "2026-10-31T11:59:54" };
const RESPONSE = {
  text: "Your refund is on its way",
  at: "2026-10-31T12:00:10",
};
const DELEGATED = {
  seq: 1,
  type: "delegated",
  worker_id: "r/order_lookup",
  delegation_id: "d1",
  task: "inspect order 1042",
  at: "2026-10-31T11:59:55.900",
  attribution: "reported",
};
const LOOKUP = {
  seq: 2,
  type: "tool_call",
  worker_id: "r/order_lookup",
  delegation_id: "d1",
  tool: "get_order",
  status: "error",
  input: { order_id: "1042" },
  output: "store unavailable",
  at: "2026-10-31T11:59:57",
  attribution: "reported",
};
const RETURNED = {
  seq: 3,
  type: "returned",
  worker_id: "r/order_lookup",
  delegation_id: "d1",
  result: "eligible",
  at: "2026-10-31T11:59:59",
  attribution: "reported",
};
const REPLY_CALL = {
  seq: 4,
  type: "tool_call",
  worker_id: null,
  tool: "send_reply",
  status: "ok",
  at: "2026-10-31T12:00:09",
  attribution: "reported",
};

// One hand-off with a failed tool call and its return, then a call of the coordinator's own.
const timeline = ({
  returned = RETURNED,
  request = REQUEST,
  response = RESPONSE,
  ...rest
} = {}) => ({
  collected_at: "2026-10-31T12:00:00Z",
  started_at: "2026-10-31T11:59:50",
  coordinator: { name: "Returns coordinator" },
  workers: WORKERS,
  events: [DELEGATED, LOOKUP, returned, REPLY_CALL].filter(Boolean),
  steps: [
    handOffStep(1, {
      worker_id: "r/order_lookup",
      handoff: DELEGATED,
      returned,
    }),
    callStep(LOOKUP, { parent: 1, depth: 1 }),
    callStep(REPLY_CALL),
  ],
  turns: oneTurn(request, response),
  ...rest,
});
const TIMELINE = timeline();

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
      data: timeline({
        returned: {
          ...RETURNED,
          result: "{'valid': False, 'feedback': 'Too_short'}",
        },
      }),
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
      data: timeline({ returned: { ...RETURNED, result: answer } }),
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
      data: timeline({
        workers: WORKERS.slice(1),
        events: [REPLY_CALL],
        steps: [callStep(REPLY_CALL)],
      }),
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
      data: timeline({
        coordinator: null,
        request: null,
        response: null,
        returned: null,
      }),
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
    // A return whose hand-off the record never saw stands alone, its place unknown.
    const orphan = {
      ...RETURNED,
      seq: 1,
      result: "**Order 1042**\n- Delivered\n- Inside the window",
    };
    result = {
      data: timeline({
        events: [orphan],
        steps: [
          handOffStep(1, {
            worker_id: "r/order_lookup",
            returned: orphan,
            relationship: "unknown",
          }),
        ],
      }),
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
      data: timeline({
        returned: { ...RETURNED, result: "Done ![](https://host/x?d=secret)" },
      }),
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("says a call no agent can be named for is unknown, not the coordinator's", () => {
    const unattributed = {
      seq: 1,
      type: "tool_call",
      worker_id: null,
      tool: "get_order",
      status: "ok",
      at: "t1",
      attribution: "unattributed",
    };
    result = {
      data: timeline({
        events: [unattributed],
        steps: [callStep(unattributed)],
      }),
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
    const event = (seq, type, id, extra) => ({
      seq,
      type,
      worker_id: `r/${id}`,
      attribution: "reported",
      ...extra,
    });
    const record = (workers, events, steps) => ({
      data: timeline({ workers, events, steps }),
      isLoading: false,
      isError: false,
    });

    it("credits a nested hand-off to the agent that made it, and keeps a control pass with no answer", () => {
      const pass = event(1, "transferred", "inspiration", {
        by: null,
        delegation_id: "d1",
        at: at(1),
      });
      const handOff = event(2, "delegated", "place", {
        by: "r/inspiration",
        delegation_id: "d2",
        parent_delegation_id: "d1",
        task: "find places",
        at: at(2),
      });
      const back = event(3, "returned", "place", {
        delegation_id: "d2",
        result: "places",
        at: at(5),
      });
      result = record(
        [worker("inspiration"), worker("place")],
        [pass, handOff, back],
        [
          handOffStep(1, { worker_id: "r/inspiration", handoff: pass }),
          handOffStep(2, {
            parent: 1,
            depth: 2,
            worker_id: "r/place",
            handoff: handOff,
            returned: back,
          }),
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
      const [, passRow, handOffRow] = screen.getAllByTestId("raw-event");
      expect(passRow).toHaveTextContent("→ inspiration worker");
      expect(handOffRow).toHaveTextContent("inspiration worker");
      expect(handOffRow).toHaveTextContent("→ place worker");
    });

    it("marks the hand-offs the API says ran in parallel", () => {
      const out = (seq, id, task, second) =>
        event(seq, "delegated", id, {
          delegation_id: `d${seq}`,
          task,
          at: at(second),
        });
      const back = (seq, id, delegation, text, second) =>
        event(seq, "returned", id, {
          delegation_id: `d${delegation}`,
          result: text,
          at: at(second),
        });
      const events = [
        out(1, "research", "read", 1),
        out(2, "code", "write", 2),
        back(3, "research", 1, "notes", 5),
        back(4, "code", 2, "patch", 6),
        out(5, "review", "check", 7),
        back(6, "review", 5, "ok", 8),
      ];
      result = record(
        [worker("research"), worker("code"), worker("review")],
        events,
        [
          handOffStep(1, {
            worker_id: "r/research",
            handoff: events[0],
            returned: events[2],
            parallel: true,
          }),
          handOffStep(2, {
            worker_id: "r/code",
            handoff: events[1],
            returned: events[3],
            parallel: true,
          }),
          handOffStep(5, {
            worker_id: "r/review",
            handoff: events[4],
            returned: events[5],
          }),
        ],
      );
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getAllByText("in parallel")).toHaveLength(2);
    });

    it("says why a step's payload, later steps or a return are missing when the record reached its limit", () => {
      const toB = event(1, "delegated", "b", {
        delegation_id: "d1",
        task: "two",
        at: at(1),
      });
      const toA = event(2, "delegated", "a", {
        delegation_id: "d2",
        task: "one",
        at: at(1),
      });
      const lookup = event(3, "tool_call", "a", {
        delegation_id: "d2",
        tool: "lookup",
        status: "ok",
        input: null,
        output: null,
        input_omitted: true,
        output_omitted: true,
        at: at(2),
      });
      const fromA = event(4, "returned", "a", {
        delegation_id: "d2",
        result: "x",
        at: at(3),
      });
      result = record(
        [worker("a"), worker("b")],
        [toB, toA, lookup, fromA],
        [
          handOffStep(1, { worker_id: "r/b", handoff: toB }),
          handOffStep(2, { worker_id: "r/a", handoff: toA, returned: fromA }),
          callStep(lookup, { parent: 2, depth: 1 }),
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
      const handOff = event(1, "delegated", "a", {
        delegation_id: "d1",
        task: "many",
        at: at(1),
      });
      const calls = Array.from({ length: 21 }, (_, index) =>
        event(index + 2, "tool_call", "a", {
          delegation_id: "d1",
          tool: `tool_${index}`,
          status: "ok",
          input: { n: index },
          at: at(2),
        }),
      );
      result = record(
        [worker("a")],
        [handOff, ...calls],
        [
          handOffStep(1, { worker_id: "r/a", handoff: handOff }),
          ...calls.map((one) => callStep(one, { parent: 1, depth: 1 })),
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
    const handoff = DELEGATED;
    const call = LOOKUP;
    const reply = RETURNED;
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

    it("shows a worker's model and tokens once, under its first hand-off, when the app sent them", () => {
      result = {
        data: {
          ...twoTurns(),
          workers: WORKERS.map((one) =>
            one.worker_id === handoff.worker_id
              ? { ...one, model: "worker-model", tokens: 1200 }
              : one,
          ),
        },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      // The total of all its hand-offs, so it is not repeated as if each cost that much.
      expect(screen.getAllByText("worker-model · 1,200 tokens")).toHaveLength(
        1,
      );
      expect(screen.getAllByTestId("turn")[0]).toHaveTextContent(
        "worker-model · 1,200 tokens",
      );
    });

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
        turns: [{ turn: 1, request: REQUEST, response: RESPONSE }],
      },
      isLoading: false,
      isError: false,
    });

    it("opens a step's trace in Traces, at the step's own span", () => {
      const onOpenTrace = vi.fn();
      result = tracedCall();
      render(
        <CallWorkersPanel
          callExecutionId="c1"
          onOpenTrace={onOpenTrace}
          traceIds={["tr-1"]}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Open in Traces" }));
      expect(onOpenTrace).toHaveBeenCalledWith("tr-1", "sp-1");
    });

    it("opens a hand-off's trace at the span it was made in", () => {
      const onOpenTrace = vi.fn();
      const traced = { ...handoff, trace_id: "tr-1", span_id: "sp-handoff" };
      result = {
        data: timeline({
          events: [traced],
          steps: [
            handOffStep(1, { worker_id: traced.worker_id, handoff: traced }),
          ],
        }),
        isLoading: false,
        isError: false,
      };
      render(
        <CallWorkersPanel
          callExecutionId="c1"
          onOpenTrace={onOpenTrace}
          traceIds={["tr-1"]}
        />,
      );
      fireEvent.click(
        screen.getByRole("button", {
          name: /^Show raw data for Order-lookup worker/,
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Open in Traces" }));
      expect(onOpenTrace).toHaveBeenCalledWith("tr-1", "sp-handoff");
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
      expect(
        screen.queryByRole("button", { name: "Open in Traces" }),
      ).toBeNull();
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
        },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(
        screen.getByText(/Nothing was recorded for this call's participants/),
      ).toBeInTheDocument();
      // No empty step list under the note.
      expect(screen.queryByTestId("raw-events")).toBeNull();
    });

    it.each(["partial", "unavailable"])(
      "does not call a return missing after a %s read a failure",
      (collection) => {
        result = {
          data: timeline({ returned: null, collection }),
          isLoading: false,
          isError: false,
        };
        render(<CallWorkersPanel callExecutionId="c1" />);
        expect(screen.getByTestId("worker-row")).toHaveAttribute(
          "data-end",
          "cut_off",
        );
        expect(
          screen.getByText(
            "Return from Order-lookup worker not shown: this call's record is incomplete",
          ),
        ).toBeInTheDocument();
        expect(screen.queryByText(/^No return from/)).toBeNull();
      },
    );

    it("says when only part of the call's traces could be read", () => {
      result = {
        data: { ...TIMELINE, collection: "partial" },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(
        screen.getByText(/Only part of this call's traces could be read/),
      ).toBeInTheDocument();
    });

    it("says when the call's traces could not be read", () => {
      result = {
        data: { ...TIMELINE, collection: "unavailable" },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(
        screen.getByText(/This call's traces could not be read/),
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
        }),
        callStep(EVENTS[1], { parent: 1, depth: 1 }),
        handOffStep(4, {
          worker_id: "r/refund",
          handoff: EVENTS[3],
          returned: EVENTS[5],
        }),
        callStep(EVENTS[4], { parent: 4, depth: 1 }),
      ],
    });
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
