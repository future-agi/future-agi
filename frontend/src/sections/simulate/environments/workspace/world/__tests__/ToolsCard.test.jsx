import { describe, it, expect } from "vitest";
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
    render(<ToolsCard tools={tools} />);
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
          ...tools,
        ]}
      />,
    );
    expect(screen.getByText("Tools · 4")).toBeInTheDocument();
    const headings = [
      "Simulated",
      "Runs inside the agent",
      "Unsupported",
      "Where it runs is not known",
    ].map((label) => screen.getByText(label));
    headings.reduce((previous, current) => {
      expect(
        previous.compareDocumentPosition(current) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      return current;
    });
    expect(
      screen.getByText(
        "These reach a system the test world has no copy of, so a test never calls them. 1 tool.",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("group", { name: "Unsupported" })).getByText(
        "charge_card",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("group", { name: "Simulated" })).queryByText(
        "charge_card",
      ),
    ).toBeNull();
  });

  it("shows a tool's arguments with units, what it changes and its reset", () => {
    render(<ToolsCard tools={tools} />);
    expect(screen.getByText("orders POST /orders/refund")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Show the contract of issue_refund" }),
    );
    const detail = within(toolOf("issue_refund"));
    expect(detail.getByText("Arguments")).toBeInTheDocument();
    expect(
      screen.getByText("refund_amount: number, cents, USD"),
    ).toBeInTheDocument();
    expect(screen.getByText("order_id: string")).toBeInTheDocument();
    expect(screen.getByText("refund_id: string")).toBeInTheDocument();
    expect(screen.getByText("amount: integer, cents, USD")).toBeInTheDocument();
    expect(detail.getByText("Changes")).toBeInTheDocument();
    expect(screen.getByText("Can change records")).toBeInTheDocument();
    expect(
      screen.getByText("Reads orders; changes refunds, orders"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("adds a refund row and marks the order refunded"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("the order is older than 30 days"),
    ).toBeInTheDocument();
    expect(screen.getByText("lookup_order")).toBeInTheDocument();
    expect(detail.getByText("Reset")).toBeInTheDocument();
    expect(
      screen.getByText("Reset to the seeded records before each trial"),
    ).toBeInTheDocument();
  });

  it("says an in-process tool is not reset, as the contract states", () => {
    render(<ToolsCard tools={tools} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show the contract of summarise" }),
    );
    expect(screen.getByText("Not reset by the test world")).toBeInTheDocument();
    expect(
      screen.getAllByText("Not recorded for this environment version").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Not declared")).toBeInTheDocument();
    expect(screen.getByText(NOT_RECORDED)).toBeInTheDocument();
  });

  it("leaves the reset line out when the contract doesn't say", () => {
    render(<ToolsCard tools={[status("mystery", "unknown")]} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show the contract of mystery" }),
    );
    expect(within(toolOf("mystery")).queryByText("Reset")).toBeNull();
  });
});
