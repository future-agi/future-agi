import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import ToolsCard from "../ToolsCard";

const NOT_RECORDED =
  "Which tables it reads and changes is not recorded for this environment version";
const status = (tool, value, extra = {}) => ({
  tool,
  status: value,
  reset_between_trials: null,
  runs_at: null,
  writes: false,
  description: "",
  arguments: [],
  returns: [],
  requires: [],
  refuses: "",
  effect: "unknown",
  effects: "",
  tables: { recorded: false, read: [], changed: [], label: NOT_RECORDED },
  ...extra,
});

const toolOf = (name) =>
  screen.getByRole("button", { name: `Show the contract of ${name}` })
    .parentElement;

const tools = [
  status("issue_refund", "simulated", {
    reset_between_trials: true,
    runs_at: "orders POST /orders/refund",
    writes: true,
    description: "Refunds a charge.",
    arguments: [
      { name: "refund_amount", type: "number", unit: "cents, USD" },
      { name: "order_id", type: "string", unit: null },
    ],
    returns: [
      { name: "refund_id", kind: "string" },
      { name: "amount", kind: "integer, cents, USD" },
    ],
    refuses: "the order is older than 30 days",
    effects: "adds a refund row and marks the order refunded",
    requires: ["lookup_order"],
    effect: "write",
    tables: {
      recorded: true,
      read: ["orders"],
      changed: ["refunds", "orders"],
      label: "Reads orders; changes refunds, orders",
    },
  }),
  status("summarise", "in_process", {
    reset_between_trials: false,
    description: "Summarises the call.",
  }),
];

describe("ToolsCard", () => {
  it("never names ALK in the card's words", () => {
    render(<ToolsCard tools={tools} onOpenTool={vi.fn()} />);
    expect(
      screen.getByText("The tools the agent declares, and where each one runs"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/ALK/)).toBeNull();
  });

  it("groups the tools by status, in a fixed order, with a count each", () => {
    render(
      <ToolsCard
        tools={[
          status("mystery", "unknown"),
          status("charge_card", "unsupported"),
          status("no_status", undefined),
          ...tools,
        ]}
        onOpenTool={vi.fn()}
      />,
    );
    expect(screen.getByText("Tools · 5")).toBeInTheDocument();
    const groups = screen.getAllByRole("group");
    expect(groups.map((group) => group.getAttribute("aria-label"))).toEqual([
      "Simulated",
      "Runs inside the agent",
      "Unsupported",
      "Not known",
    ]);
    expect(
      screen.getByText(
        "Reaches a system the world has no copy of, so a run never calls it. · 1 tool",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The contract doesn't say where these run. · 2 tools"),
    ).toBeInTheDocument();
    const unsupported = within(
      screen.getByRole("group", { name: "Unsupported" }),
    );
    expect(unsupported.getByText("charge_card")).toBeInTheDocument();
    const unknown = within(screen.getByRole("group", { name: "Not known" }));
    expect(unknown.getByText("mystery")).toBeInTheDocument();
    expect(unknown.getByText("no_status")).toBeInTheDocument();
    expect(
      within(screen.getByRole("group", { name: "Simulated" })).queryByText(
        "charge_card",
      ),
    ).toBeNull();
  });

  it("shows each tool's description on its row", () => {
    render(<ToolsCard tools={tools} onOpenTool={vi.fn()} />);
    const row = screen.getByRole("button", {
      name: "Show the contract of issue_refund",
    });
    expect(within(row).getByText("issue_refund")).toBeInTheDocument();
    expect(within(row).getByText("Refunds a charge.")).toBeInTheDocument();
  });

  it("hands the clicked tool to the drawer opener", () => {
    const onOpenTool = vi.fn();
    render(<ToolsCard tools={tools} onOpenTool={onOpenTool} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show the contract of summarise" }),
    );
    expect(onOpenTool).toHaveBeenCalledTimes(1);
    expect(onOpenTool).toHaveBeenCalledWith(tools[1]);
  });
});
