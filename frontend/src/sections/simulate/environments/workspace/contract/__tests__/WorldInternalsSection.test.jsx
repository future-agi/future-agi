import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import WorldInternalsSection from "../WorldInternalsSection";

const env = {
  tools: [
    {
      name: "issue_refund",
      desc: "Refunds a charge.",
      args: ["refund_amount", "order_id"],
      argTypes: { refund_amount: "number", order_id: "string" },
      argUnits: { refund_amount: "cents, USD" },
      returns: { refund_id: "string", amount: "integer, cents, USD" },
      refuses: "the order is older than 30 days",
      effects: "adds a refund row and marks the order refunded",
      requires: ["lookup_order"],
    },
    { name: "summarise", desc: "Summarises the call.", args: [] },
  ],
  toolEntrypoints: [
    {
      tool: "issue_refund",
      mode: "service",
      endpoint: "orders/refund",
      effect: "write",
    },
    { tool: "summarise", mode: "unreachable" },
  ],
  doorReach: { issue_refund: true, summarise: false },
};

describe("WorldInternalsSection tools", () => {
  it("shows a tool's inputs with units, effect and reset", () => {
    render(<WorldInternalsSection env={env} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show the contract of issue_refund" }),
    );
    expect(
      screen.getByText("refund_amount: number, cents, USD"),
    ).toBeInTheDocument();
    expect(screen.getByText("order_id: string")).toBeInTheDocument();
    expect(screen.getByText("refund_id: string")).toBeInTheDocument();
    expect(screen.getByText("amount: integer, cents, USD")).toBeInTheDocument();
    expect(screen.getByText("Can change records")).toBeInTheDocument();
    expect(
      screen.getByText("adds a refund row and marks the order refunded"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("the order is older than 30 days"),
    ).toBeInTheDocument();
    expect(screen.getByText("lookup_order")).toBeInTheDocument();
    expect(
      screen.getByText("Reset to the seeded records before each trial"),
    ).toBeInTheDocument();
    expect(screen.getByText("Simulated")).toBeInTheDocument();
  });

  it("labels an in-process tool", () => {
    render(<WorldInternalsSection env={env} />);
    expect(screen.getByText("Runs inside the agent")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Show the contract of summarise" }),
    );
    expect(screen.getByText("Not reset by the test world")).toBeInTheDocument();
    // A contract written before returned fields were recorded says so rather than showing nothing.
    expect(
      screen.getAllByText("Not recorded for this environment version").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Not declared")).toBeInTheDocument();
    expect(screen.getAllByText("Simulated")).toHaveLength(1);
  });

  it("marks a tool that reaches production Unsupported, never Simulated or in-process", () => {
    const tools = [
      ...env.tools,
      { name: "charge_card", desc: "Charges the card.", args: ["amount"] },
    ];
    render(
      <WorldInternalsSection
        env={{ ...env, tools, productionOnly: ["charge_card"] }}
      />,
    );
    expect(screen.getAllByText("Unsupported")).toHaveLength(1);
    expect(screen.getAllByText("Runs inside the agent")).toHaveLength(1);
    expect(screen.getAllByText("Simulated")).toHaveLength(1);
  });
});
