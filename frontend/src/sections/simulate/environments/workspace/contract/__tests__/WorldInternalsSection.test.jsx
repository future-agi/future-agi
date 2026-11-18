import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import WorldInternalsSection from "../WorldInternalsSection";

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

const env = {
  toolStatus: [
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
  ],
};

describe("WorldInternalsSection tables", () => {
  it("lists only the world's own tables the detail sends, with their fields and the rows in all", () => {
    render(
      <WorldInternalsSection
        env={{
          ...env,
          worldTables: [
            {
              name: "orders",
              rows: 3,
              note: "world_db · postgres",
              fields: [
                { name: "order_id", type: "str" },
                { name: "amount", type: "float" },
              ],
            },
            { name: "refunds", rows: 1, note: null, fields: [] },
          ],
          worldTotalRows: 4,
        }}
      />,
    );
    expect(screen.getByText("2 tables · 4 rows")).toBeInTheDocument();
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(
      screen.getByText("2 fields · world_db · postgres"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/_alk_/)).toBeNull();
    fireEvent.click(screen.getByText("orders"));
    expect(screen.getByText("order_id")).toBeInTheDocument();
    expect(screen.getByText("float")).toBeInTheDocument();
  });

  it("shows no tables card when the detail sends none", () => {
    render(<WorldInternalsSection env={env} />);
    expect(screen.queryByText("World state · database")).toBeNull();
  });
});

describe("WorldInternalsSection tools", () => {
  it("never names ALK in the card's words", () => {
    render(<WorldInternalsSection env={env} />);
    expect(
      screen.getByText("The tools the agent declares, and where each one runs"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/ALK/)).toBeNull();
  });

  it("shows a tool's arguments with units, what it changes and its reset", () => {
    render(<WorldInternalsSection env={env} />);
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
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    expect(screen.queryByText("Inputs")).toBeNull();
    expect(screen.queryByText("Between trials")).toBeNull();
  });

  it("labels an in-process tool and says it is not reset, as the contract states", () => {
    render(<WorldInternalsSection env={env} />);
    expect(screen.getByText("Runs inside the agent")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Show the contract of summarise" }),
    );
    expect(screen.getByText("Not reset by the test world")).toBeInTheDocument();
    expect(
      screen.getAllByText("Not recorded for this environment version").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Not declared")).toBeInTheDocument();
    expect(screen.getByText(NOT_RECORDED)).toBeInTheDocument();
    expect(screen.getAllByText("Simulated")).toHaveLength(1);
  });

  it("marks a tool the contract calls unsupported, never Simulated or in-process, and an unknown one with no chip", () => {
    render(
      <WorldInternalsSection
        env={{
          ...env,
          toolStatus: [
            ...env.toolStatus,
            status("charge_card", "unsupported"),
            status("mystery", "unknown"),
          ],
        }}
      />,
    );
    expect(screen.getAllByText("Unsupported")).toHaveLength(1);
    expect(screen.getAllByText("Runs inside the agent")).toHaveLength(1);
    expect(screen.getAllByText("Simulated")).toHaveLength(1);
    fireEvent.click(
      screen.getByRole("button", { name: "Show the contract of mystery" }),
    );
    expect(within(toolOf("mystery")).queryByText("Reset")).toBeNull();
  });
});
