import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

let result;
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useCallWorkers: () => result,
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
    // What was seen, and no scope note when the call stayed within it.
    expect(
      screen.getByText("One level, one hand-off at a time"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Checked for one coordinator/)).toBeNull();
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

  it("opens with what the tab shows, and names only the tool inside its worker's hand-off", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByText(
        "How the coordinator handed work to other agents in this call: each hand-off, the tools that agent used, and what it returned.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("get_order (error)")).toBeInTheDocument();
    expect(screen.queryByText(/Order-lookup worker · get_order/)).toBeNull();
    // A call outside any hand-off still says whose it was.
    expect(screen.getByText("Coordinator · send_reply")).toBeInTheDocument();
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

  it("says plainly when an agent with no workers handed nothing out, but not when declared workers went unseen", () => {
    const single = {
      ...TIMELINE,
      workers: [],
      events: [TIMELINE.events[3]],
    };
    result = { data: single, isLoading: false, isError: false };
    const { unmount } = render(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByText(
        "This agent didn't hand work to other agents in this call.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Caller's request")).toBeNull();
    unmount();
    result = {
      data: { ...single, workers: TIMELINE.workers.slice(1) },
      isLoading: false,
      isError: false,
    };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.queryByText(
        "This agent didn't hand work to other agents in this call.",
      ),
    ).toBeNull();
    expect(screen.getByText("Audit worker · not observed")).toBeInTheDocument();
  });

  it("times each step from the call's start", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("+4.0 s")).toBeInTheDocument();
    expect(screen.getByText("+5.9 s")).toBeInTheDocument();
    expect(screen.getByText("+7.0 s")).toBeInTheDocument();
    expect(screen.getByText("+20.0 s")).toBeInTheDocument();
  });

  it("opens a tool call to show its input and output", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.queryByText("store unavailable")).toBeNull();
    fireEvent.click(screen.getByText(/^get_order/));
    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(screen.getByText("store unavailable")).toBeInTheDocument();
  });

  it("says Unknown for a request, reply, coordinator or return that was not seen", () => {
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
    expect(screen.getByText("Coordinator · Unknown")).toBeInTheDocument();
    expect(screen.getAllByText("Unknown")).toHaveLength(2);
    expect(
      screen.getByText("No return from Order-lookup worker before the call ended"),
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

  it("marks a declared worker that sent nothing as not observed", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(screen.getByText("Audit worker · not observed")).toBeInTheDocument();
  });

  it("says plainly when the call has no worker record, and when it could not be read", () => {
    result = { data: null, isLoading: false, isError: false };
    const { unmount } = render(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByText(/No workers recorded for this call/),
    ).toBeInTheDocument();
    unmount();
    result = { data: undefined, isLoading: false, isError: true };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByText("Couldn't load this call's workers."),
    ).toBeInTheDocument();
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
      // The request sits above the root and the reply after it.
      const request = screen.getByText("Caller's request");
      const reply = screen.getByText("Coordinator's reply");
      expect(
        request.compareDocumentPosition(root) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(
        root.compareDocumentPosition(reply) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(root.contains(reply)).toBe(false);
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
    });

    const depthOf = (text) =>
      screen
        .getByText(text, { exact: false })
        .closest("[data-testid='hand-off']")
        .getAttribute("data-depth");

    it("nests a hand-off under the worker that made it, and says how deep the call went", () => {
      result = timeline(
        [worker("planner"), worker("search")],
        [
          {
            type: "delegated",
            worker_id: "r/planner",
            by: null,
            task: "plan the trip",
            at: at(1),
          },
          {
            type: "delegated",
            worker_id: "r/search",
            by: "r/planner",
            task: "find hotels",
            at: at(2),
          },
          {
            type: "tool_call",
            worker_id: "r/search",
            tool: "hotels",
            status: "ok",
            at: at(3),
          },
          {
            type: "returned",
            worker_id: "r/search",
            result: "two hotels",
            at: at(4),
          },
          {
            type: "returned",
            worker_id: "r/planner",
            result: "a plan",
            at: at(5),
          },
        ],
      );
      render(<CallWorkersPanel callExecutionId="c1" />);
      expect(depthOf("Delegated to planner worker")).toBe("1");
      expect(depthOf("Delegated to search worker")).toBe("2");
      expect(
        screen
          .getByText("Delegated to search worker", { exact: false })
          .closest("[data-depth='1']"),
      ).not.toBeNull();
      expect(
        screen.getByText("2 levels deep · one hand-off at a time"),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Checked for one coordinator/),
      ).toBeInTheDocument();
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
        screen.getByText("20 workers · 15 observed · 5 not observed"),
      ).toBeInTheDocument();
      expect(screen.queryByText("w3 worker")).toBeNull();
      fireEvent.click(screen.getByText("Show all workers"));
      const chip = (name) => screen.getByText(name).closest(".MuiChip-root");
      expect(chip("w0 worker").className).toMatch(/colorInfo/);
      expect(chip("w1 worker").className).toMatch(/colorSuccess/);
      expect(chip("w2 worker").className).toMatch(/colorSuccess/);
    });

    it("says why a step's payload or later steps are missing when the record reached its limit", () => {
      result = timeline(
        [worker("a")],
        [
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
      result.data.events_omitted = 12;
      render(<CallWorkersPanel callExecutionId="c1" />);
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

    it("renders a hundred workers three levels deep with a thousand steps, folding the deep levels", () => {
      const team = Array.from({ length: 100 }, (_, index) =>
        worker(`w${index}`),
      );
      const big = "x".repeat(2000);
      const events = [];
      let second = 0;
      const stamp = () => {
        second += 1;
        return new Date(Date.UTC(2026, 9, 31, 12, 0, 0) + second * 100)
          .toISOString()
          .slice(0, 23);
      };
      for (let top = 0; top < 10; top += 1) {
        const lead = `r/w${top}`;
        events.push({
          type: "delegated",
          worker_id: lead,
          task: "lead",
          at: stamp(),
        });
        for (let mid = 0; mid < 3; mid += 1) {
          const helper = `r/w${10 + top * 3 + mid}`;
          events.push({
            type: "delegated",
            worker_id: helper,
            by: lead,
            task: "help",
            at: stamp(),
          });
          for (let low = 0; low < 2; low += 1) {
            const leaf = `r/w${40 + ((top * 6 + mid * 2 + low) % 60)}`;
            events.push({
              type: "delegated",
              worker_id: leaf,
              by: helper,
              task: "do",
              at: stamp(),
            });
            for (let call = 0; call < 10; call += 1)
              events.push({
                type: "tool_call",
                worker_id: leaf,
                tool: "t",
                status: "ok",
                input: { big },
                output: big,
                at: stamp(),
              });
            events.push({
              type: "returned",
              worker_id: leaf,
              result: "leaf done",
              at: stamp(),
            });
          }
          for (let call = 0; call < 7; call += 1)
            events.push({
              type: "tool_call",
              worker_id: helper,
              tool: "t",
              status: "ok",
              input: { big },
              output: big,
              at: stamp(),
            });
          events.push({
            type: "returned",
            worker_id: helper,
            result: "helper done",
            at: stamp(),
          });
        }
        events.push({
          type: "returned",
          worker_id: lead,
          result: "lead done",
          at: stamp(),
        });
      }
      result = timeline(team, events);
      const began = performance.now();
      render(<CallWorkersPanel callExecutionId="c1" />);
      const took = performance.now() - began;
      expect(events.length).toBeGreaterThanOrEqual(1000);
      expect(
        screen.getByText("100 workers · 100 observed · 0 not observed"),
      ).toBeInTheDocument();
      expect(
        screen.getByText("3 levels deep · one hand-off at a time"),
      ).toBeInTheDocument();
      // Levels one and two are open; level three shows its 60 headers folded, so its 600 steps are not in the page.
      expect(screen.getAllByTestId("hand-off")).toHaveLength(100);
      expect(screen.queryByText("leaf done")).toBeNull();
      expect(screen.getAllByText(/^t$/)).toHaveLength(210);
      expect(took).toBeLessThan(3000);
    });
  });
});

describe("readable", () => {
  it("shows a structured task or answer as its own text", async () => {
    const { readable } = await import("../CallWorkersPanel");
    expect(
      readable(
        '{"description": "Implement a rate limiter", "subagent_type": "coder"}',
      ),
    ).toBe("Implement a rate limiter");
    expect(
      readable(
        '{"messages": ["content=\'Build it\' additional_kwargs={}", "content=\'Done: limiter.py\' additional_kwargs={}"]}',
      ),
    ).toBe("Done: limiter.py");
    expect(readable("content='plain answer' response_metadata={}")).toBe(
      "plain answer",
    );
    expect(readable("Refund order #1043 in full")).toBe(
      "Refund order #1043 in full",
    );
    expect(readable('{"order_id": 1043}')).toBe('{"order_id": 1043}');
    expect(
      readable(
        '{"messages": ["content=\'Build a limiter\' additional_kwargs={}", "content=\'\' addi…',
      ),
    ).toBe(null);
  });
});
