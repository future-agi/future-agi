import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

let result;
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useCallWorkers: () => result,
}));

const { default: CallWorkersPanel } = await import("../CallWorkersPanel");

const TIMELINE = {
  collected_at: "2026-10-31T12:00:00Z",
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
      at: "t1",
      attribution: "reported",
    },
    {
      seq: 2,
      type: "tool_call",
      worker_id: "r/order_lookup",
      tool: "get_order",
      status: "error",
      at: "t2",
      attribution: "reported",
    },
    {
      seq: 3,
      type: "returned",
      worker_id: "r/order_lookup",
      result: "eligible",
      at: "t3",
      attribution: "reported",
    },
    {
      seq: 4,
      type: "tool_call",
      worker_id: null,
      tool: "send_reply",
      status: "ok",
      at: "t4",
      attribution: "reported",
    },
  ],
};

describe("CallWorkersPanel", () => {
  beforeEach(() => {
    result = { data: TIMELINE, isLoading: false, isError: false };
  });

  it("shows each hand-off, the worker's calls and what it returned, under the coordinator", () => {
    render(<CallWorkersPanel callExecutionId="c1" />);
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
      screen.getByText(/No worker record for this call/),
    ).toBeInTheDocument();
    unmount();
    result = { data: undefined, isLoading: false, isError: true };
    render(<CallWorkersPanel callExecutionId="c1" />);
    expect(
      screen.getByText("Couldn't load this call's workers."),
    ).toBeInTheDocument();
  });
});
