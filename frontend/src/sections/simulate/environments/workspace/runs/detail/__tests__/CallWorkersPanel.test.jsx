import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";

import { withSteps } from "./workerSteps.fixture";

let result;
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallWorkers: () => result && { ...result, data: withSteps(result.data) },
}));

const { default: CallWorkersPanel } = await import("../CallWorkersPanel");

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

  it("shows the request, each hand-off with the worker's calls and return, and the reply, under the coordinator", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByText("Coordinator · Returns coordinator"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("One level, one hand-off at a time"),
    ).toBeInTheDocument();
    expect(screen.getByText("Please refund order 1042")).toBeInTheDocument();
    expect(
      screen.getByText("Delegated to Order-lookup worker: inspect order 1042"),
    ).toBeInTheDocument();
    expect(screen.getByText(/^get_order/)).toBeInTheDocument();
    expect(screen.getByText(/\(error\)/)).toBeInTheDocument();
    expect(
      screen.getByText("Order-lookup worker returned"),
    ).toBeInTheDocument();
    expect(screen.getByText("eligible")).toBeInTheDocument();
    expect(screen.getByText("Coordinator · send_reply")).toBeInTheDocument();
    expect(screen.getByText("Your refund is on its way")).toBeInTheDocument();
    // In order: request, hand-off, return, the coordinator's own call, the reply.
    const order = [
      "Please refund order 1042",
      "Order-lookup worker returned",
      "Coordinator · send_reply",
      "Your refund is on its way",
    ].map((text) => screen.getByText(text));
    order
      .slice(1)
      .forEach((node, index) =>
        expect(
          order[index].compareDocumentPosition(node) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBeTruthy(),
      );
  });

  it("says when the record was built without the app's traces, and not otherwise", () => {
    const note = /the app sent no traces, so the Traces tab is empty/;
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

  it("names only the tool inside its worker's hand-off", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("get_order (error)")).toBeInTheDocument();
    expect(screen.queryByText(/Order-lookup worker · get_order/)).toBeNull();
    // A call outside any hand-off still says whose it was.
    expect(screen.getByText("Coordinator · send_reply")).toBeInTheDocument();
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
    render(<CallWorkersPanel callExecutionId="c1" />);
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
    render(<CallWorkersPanel callExecutionId="c1" />);
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
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("Audit worker · not observed")).toBeInTheDocument();
    expect(screen.queryByText("Caller's request")).toBeNull();
    expect(screen.queryByText("Coordinator's reply")).toBeNull();
  });

  it("times each step from the call's start", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("0:04")).toBeInTheDocument();
    expect(screen.getByText("0:05")).toBeInTheDocument();
    expect(screen.getByText("0:07")).toBeInTheDocument();
    expect(screen.getByText("0:20")).toBeInTheDocument();
  });

  it("opens a tool call to show its input and output, by mouse or keyboard", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.queryByText("store unavailable")).toBeNull();
    fireEvent.click(screen.getByText(/^get_order/));
    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(screen.getByText("store unavailable")).toBeInTheDocument();
    const row = screen.getByRole("button", { name: /^get_order/ });
    expect(row).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(row, { key: " " });
    expect(row).toHaveAttribute("aria-expanded", "false");
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
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("Coordinator")).toBeInTheDocument();
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
    render(<CallWorkersPanel callExecutionId="c1" />);
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
    render(<CallWorkersPanel callExecutionId="c1" />);
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
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("Unknown · get_order")).toBeInTheDocument();
    expect(screen.queryByText("Coordinator · get_order")).toBeNull();
  });

  it("marks a hand-off and a call the record can't place as parent unknown, and only those", () => {
    const handoff = TIMELINE.events[0];
    const call = TIMELINE.events[1];
    result = {
      data: {
        ...TIMELINE,
        steps: [
          {
            key: "1",
            kind: "handoff",
            parent: null,
            depth: 1,
            worker_id: handoff.worker_id,
            transfer: false,
            handoff,
            returned: null,
            parallel: false,
            turn: 1,
            relationship: "unknown",
          },
          {
            key: "2",
            kind: "call",
            parent: null,
            depth: 0,
            worker_id: call.worker_id,
            call,
            turn: 1,
            relationship: "unknown",
          },
          {
            key: "3",
            kind: "call",
            parent: null,
            depth: 0,
            worker_id: null,
            call: TIMELINE.events[3],
            turn: 1,
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
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getAllByTestId("parent-unknown")).toHaveLength(2);
  });

  it("says when nothing was recorded for the participants, and lists the declared ones as not observed", () => {
    result = {
      data: {
        ...TIMELINE,
        events: [],
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
    expect(screen.getAllByText(/not observed/)).toHaveLength(2);
  });

  it("shows a spinner until the record is read, and an error only with nothing to show", () => {
    result = { data: undefined, isLoading: true, isError: false };
    const { unmount } = render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    unmount();
    result = { data: undefined, isLoading: false, isError: true };
    const failed = render(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByText("Couldn't load this call's participants."),
    ).toBeInTheDocument();
    failed.unmount();
    // A failed refetch leaves the tree that's already on screen.
    result = { data: TIMELINE, isLoading: false, isError: true };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByTestId("tree-root")).toBeInTheDocument();
    expect(
      screen.queryByText("Couldn't load this call's participants."),
    ).toBeNull();
  });

  describe("as a tree", () => {
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
    // The guide-line branch a node hangs in, and the node that branch belongs to (the root when none).
    const ownerOf = (node) =>
      node.parentElement.closest(
        '[data-testid="hand-off"], [data-testid="tree-root"]',
      );

    it("draws the coordinator as the root and each hand-off as a child joined by guide lines", () => {
      result = { data: TIMELINE, isLoading: false, isError: false };
      render(<CallWorkersPanel callExecutionId="c1" />);
      const root = screen.getByTestId("tree-root");
      const node = screen.getByTestId("hand-off");
      // The hand-off hangs in a guide-line branch of the root, under the coordinator, not beside it.
      expect(node.closest('[data-testid="tree-child"]')).not.toBeNull();
      expect(node.closest('[data-testid="tree-branch"]')).not.toBeNull();
      expect(ownerOf(node)).toBe(root);
      expect(root).toHaveTextContent("Coordinator · Returns coordinator");
      // Each turn reads in order: the request, the hand-offs made for it, then the reply.
      const request = screen.getByText("Caller's request");
      const reply = screen.getByText("Coordinator's reply");
      expect(
        request.compareDocumentPosition(node) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(
        node.compareDocumentPosition(reply) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });

    it("draws one block per turn, each with its own request, hand-offs and reply", () => {
      const record = withSteps(TIMELINE);
      result = {
        data: {
          ...record,
          steps: record.steps.map((step, index) => ({
            ...step,
            turn: index === 0 ? 1 : 2,
          })),
          turns: [
            {
              turn: 1,
              request: { text: "first question", at: null },
              response: { text: "first answer", at: null },
            },
            {
              turn: 2,
              request: { text: "second question", at: null },
              response: null,
            },
          ],
        },
        isLoading: false,
        isError: false,
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      const blocks = screen.getAllByTestId("turn");
      expect(blocks).toHaveLength(2);
      expect(blocks[0]).toHaveTextContent("Turn 1 · Caller's request");
      expect(blocks[0]).toHaveTextContent("first answer");
      expect(blocks[1]).toHaveTextContent("second question");
      expect(blocks[1]).toHaveTextContent("No reply recorded");
      // The hand-off made in turn 1 is drawn in turn 1's block only.
      expect(within(blocks[0]).getAllByTestId("hand-off")).toHaveLength(1);
      expect(within(blocks[1]).queryAllByTestId("hand-off")).toHaveLength(0);
    });

    it("hangs a nested hand-off under the node of the agent that made it", () => {
      result = timeline(
        [worker("lead"), worker("helper")],
        [
          { type: "delegated", worker_id: "r/lead", at: at(1) },
          { type: "delegated", worker_id: "r/helper", by: "r/lead", at: at(2) },
          { type: "returned", worker_id: "r/helper", result: "h", at: at(3) },
          { type: "returned", worker_id: "r/lead", result: "l", at: at(4) },
        ],
      );
      render(<CallWorkersPanel callExecutionId="c1" />);
      const [lead, helper] = screen.getAllByTestId("hand-off");
      expect(ownerOf(lead)).toBe(screen.getByTestId("tree-root"));
      expect(ownerOf(helper)).toBe(lead);
      expect(helper.closest('[data-testid="tree-branch"]')).not.toBe(
        lead.closest('[data-testid="tree-branch"]'),
      );
      expect(
        screen.getByText("2 levels deep · one hand-off at a time"),
      ).toBeInTheDocument();
    });

    it("shows a control pass as its own node, with the hand-offs that agent made under it", () => {
      result = timeline(
        [worker("inspiration"), worker("place"), worker("poi")],
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
            at: null,
          },
          {
            type: "returned",
            worker_id: "r/place",
            result: "places",
            at: at(5),
          },
          {
            type: "delegated",
            worker_id: "r/poi",
            by: "r/inspiration",
            at: null,
          },
          { type: "returned", worker_id: "r/poi", result: "sights", at: at(9) },
        ],
      );
      render(<CallWorkersPanel callExecutionId="c1" />);
      const nodes = screen.getAllByTestId("hand-off");
      const passed = nodes.find(
        (one) => one.dataset.worker === "inspiration worker",
      );
      expect(passed).toHaveTextContent("Control passed to inspiration worker");
      expect(passed).not.toHaveTextContent("inspiration worker returned");
      for (const name of ["place worker", "poi worker"])
        expect(ownerOf(nodes.find((one) => one.dataset.worker === name))).toBe(
          passed,
        );
      // Neither sibling has a start, so "one at a time" would be a guess.
      expect(
        screen.getByText(/can't tell if hand-offs overlapped/),
      ).toBeInTheDocument();
    });

    const depthOf = (text) =>
      screen
        .getByText(text, { exact: false })
        .closest("[data-testid='hand-off']")
        .getAttribute("data-depth");

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
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getAllByText("in parallel")).toHaveLength(2);
      expect(
        screen.getByText("One level · 2 hand-offs ran in parallel"),
      ).toBeInTheDocument();
    });

    it("summarises a large team in one line and colours by depth, not by worker", () => {
      const team = Array.from({ length: 20 }, (_, index) =>
        worker(`w${index}`, index < 15),
      );
      result = timeline(team, [
        { type: "delegated", worker_id: "r/w0", task: "lead", at: at(1) },
        {
          type: "delegated",
          worker_id: "r/w1",
          by: "r/w0",
          task: "help",
          at: at(2),
        },
        { type: "returned", worker_id: "r/w1", result: "done", at: at(3) },
        {
          type: "delegated",
          worker_id: "r/w2",
          by: "r/w0",
          task: "help",
          at: at(4),
        },
        { type: "returned", worker_id: "r/w2", result: "done", at: at(5) },
        { type: "returned", worker_id: "r/w0", result: "done", at: at(6) },
      ]);
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(
        screen.getByText("20 participants · 15 observed · 5 not observed"),
      ).toBeInTheDocument();
      expect(screen.queryByText("w3 worker")).toBeNull();
      fireEvent.click(screen.getByText("Show all participants"));
      const tint = (name) =>
        getComputedStyle(screen.getByText(name).closest(".MuiChip-root"))
          .backgroundColor;
      expect(tint("w1 worker")).toBe(tint("w2 worker"));
      expect(tint("w0 worker")).not.toBe(tint("w1 worker"));
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
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.getByText(/^No return from b worker/)).toBeInTheDocument();
      cleanup();
      result = {
        ...uncut,
        data: { ...uncut.data, events_omitted: 12 },
      };
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(
        screen.getByText(/^Return from b worker not shown/),
      ).toBeInTheDocument();
      expect(screen.queryByText(/^No return from/)).toBeNull();
      fireEvent.click(screen.getByText(/^lookup/));
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

    it("keeps a record from before hand-offs named their maker flat under the coordinator", () => {
      result = timeline(
        [worker("a"), worker("b")],
        [
          { type: "delegated", worker_id: "r/a", task: "one", at: at(1) },
          { type: "returned", worker_id: "r/a", result: "x", at: at(2) },
          { type: "delegated", worker_id: "r/b", task: "two", at: at(3) },
          { type: "returned", worker_id: "r/b", result: "y", at: at(4) },
        ],
      );
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(depthOf("Delegated to a worker")).toBe("1");
      expect(depthOf("Delegated to b worker")).toBe("1");
      expect(
        screen.getByText("One level, one hand-off at a time"),
      ).toBeInTheDocument();
    });

    it("keeps hand-offs below the second level folded and unmounted until opened", () => {
      result = timeline(
        [worker("a"), worker("b"), worker("c")],
        [
          { type: "delegated", worker_id: "r/a", at: at(1) },
          { type: "delegated", worker_id: "r/b", by: "r/a", at: at(2) },
          { type: "delegated", worker_id: "r/c", by: "r/b", at: at(3) },
          {
            type: "returned",
            worker_id: "r/c",
            result: "leaf done",
            at: at(4),
          },
        ],
      );
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(screen.queryByText("leaf done")).toBeNull();
      const deep = screen.getByRole("button", {
        name: /Delegated to c worker/,
      });
      expect(deep).toHaveAttribute("aria-expanded", "false");
      fireEvent.keyDown(deep, { key: "Enter" });
      expect(deep).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByText("leaf done")).toBeInTheDocument();
    });
  });
});
