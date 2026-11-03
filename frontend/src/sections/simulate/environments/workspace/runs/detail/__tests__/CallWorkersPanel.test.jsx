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
    expect(
      screen.getByText(
        /one coordinator handing work to its workers one at a time/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Please refund order 1042")).toBeInTheDocument();
    expect(
      screen.getByText("Delegated to Order-lookup worker: inspect order 1042"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Order-lookup worker · get_order/),
    ).toBeInTheDocument();
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
    fireEvent.click(screen.getByText(/Order-lookup worker · get_order/));
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
      screen.getByText("Order-lookup worker returned: Unknown"),
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
});
