import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";

let result;
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallWorkers: () => result,
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

// The API's rows, written as it sends them: simulate/services/harness_worker_steps.py arranges a call's events
// into these, decides each hand-off's end and counts, and is tested there.
const handOffStep = (
  key,
  {
    parent = null,
    depth = 1,
    worker_id,
    handoff = null,
    returned = null,
    end,
    children = [],
    parallel = false,
    relationship = "observed",
    did_not_finish = end === "failed" || end === "no_return",
    first_of_worker = true,
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
  end,
  did_not_finish,
  first_of_worker,
  children,
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
const oneTurn = (request, response, children, callsOutside = 0) => [
  {
    turn: 1,
    request,
    response,
    children,
    calls_outside: callsOutside,
    expects_messages: true,
  },
];
// Each turn's hand-offs as the API lists them: every hand-off step of that turn, in step order.
const withHandOffs = (record) => ({
  ...record,
  turns: record.turns.map((round) => ({
    handoffs: record.steps
      .filter((step) => step.kind === "handoff" && step.turn === round.turn)
      .map((step) => step.key),
    ...round,
  })),
});
// A text as the API sends it, marked as prose or data.
const prose = (text) => ({ format: "text", text });
const counts = (finished, didNotFinish, extra) => ({
  hand_offs: finished + didNotFinish,
  workers: 1,
  finished,
  did_not_finish: didNotFinish,
  passed: 0,
  cut_off: 0,
  ...extra,
});

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
const REQUEST = {
  text: "Please refund order 1042",
  at: "2026-10-31T11:59:54+00:00",
  shown: prose("Please refund order 1042"),
};
const RESPONSE = {
  text: "Your refund is on its way",
  at: "2026-10-31T12:00:10+00:00",
  shown: prose("Your refund is on its way"),
};
const DELEGATED = {
  seq: 1,
  type: "delegated",
  worker_id: "r/order_lookup",
  delegation_id: "d1",
  task: "inspect order 1042",
  task_shown: prose("inspect order 1042"),
  at: "2026-10-31T11:59:55.900+00:00",
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
  at: "2026-10-31T11:59:57+00:00",
  attribution: "reported",
};
const RETURNED = {
  seq: 3,
  type: "returned",
  worker_id: "r/order_lookup",
  delegation_id: "d1",
  result: "eligible",
  result_shown: prose("eligible"),
  status: "ok",
  at: "2026-10-31T11:59:59+00:00",
  attribution: "reported",
};
const REPLY_CALL = {
  seq: 4,
  type: "tool_call",
  worker_id: null,
  tool: "send_reply",
  status: "ok",
  at: "2026-10-31T12:00:09+00:00",
  attribution: "reported",
};

// One hand-off with a failed tool call and its return, then a call of the coordinator's own.
const timeline = ({
  returned = RETURNED,
  end = "finished",
  request = REQUEST,
  response = RESPONSE,
  ...rest
} = {}) =>
  withHandOffs({
    collected_at: "2026-10-31T12:00:00Z",
    started_at: "2026-10-31T11:59:50+00:00",
    coordinator: { name: "Returns coordinator" },
    workers: WORKERS,
    events: [DELEGATED, LOOKUP, returned, REPLY_CALL].filter(Boolean),
    steps: [
      handOffStep(1, {
        worker_id: "r/order_lookup",
        handoff: DELEGATED,
        returned,
        end,
        children: [LOOKUP.seq],
      }),
      callStep(LOOKUP, { parent: 1, depth: 1 }),
      callStep(REPLY_CALL),
    ],
    turns: oneTurn(request, response, [1, REPLY_CALL.seq], 1),
    counts: counts(end === "finished" ? 1 : 0, end === "finished" ? 0 : 1),
    cut_off: false,
    beyond_boundary: false,
    pending: false,
    state: "recorded",
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
      C.messageStep,
      C.handOffStep,
      C.toolCallStep,
      C.answerStep,
      C.toolCallStep,
      C.replyStep,
    ]);
    const [, handOff, tool] = screen.getAllByTestId("raw-event");
    expect(handOff).toHaveTextContent("Returns coordinator");
    expect(handOff).toHaveTextContent("→ Order-lookup worker");
    expect(handOff).toHaveTextContent("inspect order 1042");
    expect(tool).toHaveTextContent("Order-lookup worker");
    expect(tool).toHaveTextContent("get_order");
    expect(tool).toHaveTextContent(C.failed);
  });

  it("says when the record was built without the app's traces, and not otherwise", () => {
    const note = C.sandboxOnly;
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

  it("shows an answer the API marks as data as code, kept as it came", () => {
    const printed = "{'valid': False, 'feedback': 'Too_short'}";
    result = {
      data: timeline({
        returned: {
          ...RETURNED,
          result: printed,
          result_shown: { format: "code", text: printed },
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
      data: timeline({
        returned: { ...RETURNED, result: answer, result_shown: prose(answer) },
      }),
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    const [returned] = screen
      .getAllByTestId("answer")
      .filter((one) => one.textContent.includes("line 12"));
    expect(returned).toHaveAttribute("data-clamped", "true");
    // The short request and reply are shown whole, with nothing to open.
    expect(screen.getAllByText(C.showMore)).toHaveLength(1);
    fireEvent.click(screen.getByText(C.showMore));
    expect(returned).toHaveAttribute("data-clamped", "false");
    fireEvent.click(screen.getByText(C.showLess));
    expect(returned).toHaveAttribute("data-clamped", "true");
  });

  it("marks declared workers that sent nothing, with no request or reply when nothing was handed out", () => {
    result = {
      data: timeline({
        workers: WORKERS.slice(1),
        events: [REPLY_CALL],
        steps: [callStep(REPLY_CALL)],
        turns: oneTurn(REQUEST, RESPONSE, [REPLY_CALL.seq], 1),
        counts: counts(0, 0),
      }),
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByText(C.workersNotObserved("Audit worker")),
    ).toBeInTheDocument();
    expect(steps()).toEqual([C.toolCallStep]);
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
    const [button] = screen.getAllByRole("button", { name: C.hideJson });
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
        end: "no_return",
      }),
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getAllByText(C.coordinator).length).toBeGreaterThan(0);
    expect(screen.getByText(C.noRequest)).toBeInTheDocument();
    expect(screen.getByText(C.noReply)).toBeInTheDocument();
    expect(
      screen.getByText(C.noReturn("Order-lookup worker")),
    ).toBeInTheDocument();
  });

  it("shows what a worker returned as formatted text, not raw markdown", () => {
    // A return whose hand-off the record never saw stands alone, its place unknown.
    const text = "**Order 1042**\n- Delivered\n- Inside the window";
    const orphan = {
      ...RETURNED,
      seq: 1,
      result: text,
      result_shown: prose(text),
    };
    result = {
      data: timeline({
        events: [orphan],
        steps: [
          handOffStep(1, {
            worker_id: "r/order_lookup",
            returned: orphan,
            end: "finished",
            relationship: "unknown",
          }),
        ],
        turns: oneTurn(REQUEST, RESPONSE, [1]),
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
        returned: {
          ...RETURNED,
          result: "Done ![](https://host/x?d=secret)",
          result_shown: prose("Done ![](https://host/x?d=secret)"),
        },
      }),
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows a worker's text in angle brackets as words, not as markup to drop", () => {
    result = {
      data: timeline({
        returned: {
          ...RETURNED,
          result: "Passed to <front desk>",
          result_shown: prose("Passed to <front desk>"),
        },
      }),
      isLoading: false,
      isError: false,
    };
    render(<CallWorkersPanel callExecutionId="c1" />);
    const [cell] = screen.getAllByTestId("worker-row");
    expect(cell).toHaveTextContent("Passed to <front desk>");
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
        turns: oneTurn(REQUEST, RESPONSE, [1], 1),
        counts: counts(0, 0),
      }),
      isLoading: false,
      isError: false,
    };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByTestId("raw-event")).toHaveTextContent(C.unknown);
    expect(screen.getByTestId("raw-event")).not.toHaveTextContent(
      C.coordinator,
    );
  });

  it("shows a spinner until the record is read, and an error only with nothing to show", () => {
    result = { data: undefined, isLoading: true, isError: false };
    const { unmount } = renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    unmount();
    result = { data: undefined, isLoading: false, isError: true };
    const failed = renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText(C.error)).toBeInTheDocument();
    failed.unmount();
    // A failed refetch leaves what's already on screen.
    result = { data: TIMELINE, isLoading: false, isError: true };
    renderSteps(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByTestId("raw-events")).toBeInTheDocument();
    expect(screen.queryByText(C.error)).toBeNull();
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
      `2026-10-31T12:00:${String(second).padStart(2, "0")}+00:00`;
    const event = (seq, type, id, extra) => ({
      seq,
      type,
      worker_id: `r/${id}`,
      attribution: "reported",
      ...extra,
    });
    const record = (workers, events, steps, extra) => ({
      data: timeline({
        workers,
        events,
        steps,
        turns: oneTurn(
          REQUEST,
          RESPONSE,
          steps.filter((one) => one.parent === null).map((one) => one.key),
        ),
        ...extra,
      }),
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
          handOffStep(1, {
            worker_id: "r/inspiration",
            handoff: pass,
            end: "passed",
            children: [2],
          }),
          handOffStep(2, {
            parent: 1,
            depth: 2,
            worker_id: "r/place",
            handoff: handOff,
            returned: back,
            end: "finished",
          }),
        ],
        { counts: counts(1, 0, { hand_offs: 2, passed: 1 }) },
      );
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(steps()).toEqual([
        C.messageStep,
        C.controlPassStep,
        C.handOffStep,
        C.answerStep,
        C.replyStep,
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
            end: "finished",
            parallel: true,
          }),
          handOffStep(2, {
            worker_id: "r/code",
            handoff: events[1],
            returned: events[3],
            end: "finished",
            parallel: true,
          }),
          handOffStep(5, {
            worker_id: "r/review",
            handoff: events[4],
            returned: events[5],
            end: "finished",
          }),
        ],
      );
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getAllByText(C.inParallel)).toHaveLength(2);
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
      // The API's end for b: no return in a whole record, not shown in a cut one.
      const read = (end, extra) =>
        record(
          [worker("a"), worker("b")],
          [toB, toA, lookup, fromA],
          [
            handOffStep(1, { worker_id: "r/b", handoff: toB, end }),
            handOffStep(2, {
              worker_id: "r/a",
              handoff: toA,
              returned: fromA,
              end: "finished",
              children: [3],
            }),
            callStep(lookup, { parent: 2, depth: 1 }),
          ],
          extra,
        );
      result = read("no_return");
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByText(C.noReturn("b worker"))).toBeInTheDocument();
      cleanup();
      result = read("cut_off", { events_omitted: 12, cut_off: true });
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByText(C.returnCutOff("b worker"))).toBeInTheDocument();
      expect(screen.queryByText(C.noReturn("b worker"))).toBeNull();
      expect(screen.getAllByText(C.payloadOmitted)).toHaveLength(2);
      expect(screen.getByText(C.eventsOmitted(12))).toBeInTheDocument();
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
          handOffStep(1, {
            worker_id: "r/a",
            handoff: handOff,
            end: "no_return",
            children: calls.map((one) => one.seq),
          }),
          ...calls.map((one) => callStep(one, { parent: 1, depth: 1 })),
        ],
      );
      renderSteps(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.queryByRole("button", { name: C.hideJson })).toBeNull();
      expect(screen.getAllByRole("button", { name: C.showJson })).toHaveLength(
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
      handoff: {
        ...handoff,
        seq: key,
        task: `task ${key}`,
        task_shown: prose(`task ${key}`),
      },
      returned: { ...reply, seq: key + 100 },
      end: "finished",
      did_not_finish: false,
      first_of_worker: key === 1,
      children: [],
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
          request: { text: "first ask", at: "t1", shown: prose("first ask") },
          response: {
            text: "first reply",
            at: "t2",
            shown: prose("first reply"),
          },
          children: [1],
          calls_outside: 0,
          handoffs: [1],
          expects_messages: true,
        },
        {
          turn: 2,
          request: { text: "second ask", at: "t3", shown: prose("second ask") },
          response: {
            text: "second reply",
            at: "t4",
            shown: prose("second reply"),
          },
          children: [2],
          calls_outside: 0,
          handoffs: [2],
          expects_messages: true,
        },
      ],
      counts: counts(2, 0),
    });

    it("gives each turn its own request, hand-off table and reply", () => {
      result = { data: twoTurns(), isLoading: false, isError: false };
      render(<CallWorkersPanel callExecutionId="c1" />);
      const turns = screen.getAllByTestId("turn");
      expect(turns).toHaveLength(2);
      expect(turns[0]).toHaveTextContent(`${C.turn(1)} · ${C.request}`);
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
        turns: oneTurn(REQUEST, RESPONSE, [9], 1),
        counts: counts(0, 0),
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
      fireEvent.click(screen.getByRole("button", { name: C.openInTraces }));
      expect(onOpenTrace).toHaveBeenCalledWith("tr-1", "sp-1");
    });

    it("opens a hand-off's trace at the span it was made in", () => {
      const onOpenTrace = vi.fn();
      const traced = { ...handoff, trace_id: "tr-1", span_id: "sp-handoff" };
      result = {
        data: timeline({
          events: [traced],
          steps: [
            handOffStep(1, {
              worker_id: traced.worker_id,
              handoff: traced,
              end: "finished",
            }),
          ],
          turns: oneTurn(REQUEST, RESPONSE, [1]),
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
        screen.getByRole("button", { name: C.showRow("Order-lookup worker") }),
      );
      fireEvent.click(screen.getByRole("button", { name: C.openInTraces }));
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
      expect(screen.queryByRole("button", { name: C.openInTraces })).toBeNull();
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

    it("does not call a return missing when the API says the record was cut", () => {
      result = {
        data: timeline({
          returned: null,
          end: "cut_off",
          collection: "partial",
          cut_off: true,
        }),
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByTestId("worker-row")).toHaveAttribute(
        "data-end",
        "cut_off",
      );
      expect(
        screen.getByText(C.returnCutOff("Order-lookup worker")),
      ).toBeInTheDocument();
      expect(screen.queryByText(C.noReturn("Order-lookup worker"))).toBeNull();
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
      fireEvent.click(
        screen.getByRole("button", { name: C.showRow("Lookup") }),
      );

      const raw = screen.getByTestId("worker-row-raw");
      expect(raw).toHaveTextContent("get_order");
      expect(raw).not.toHaveTextContent("issue_refund");

      fireEvent.click(screen.getByRole("checkbox", { name: C.rawEvents }));
      expect(steps()).toContain(C.replyStep);
      expect(screen.queryByTestId("worker-row")).toBeNull();
    });
  });
});
