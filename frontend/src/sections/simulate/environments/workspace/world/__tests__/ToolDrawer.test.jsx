import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ToolDrawer from "../ToolDrawer";

const refund = {
  tool: "issue_refund",
  description: "Refunds a charge.",
  effect: "write",
  reset_between_trials: true,
  arguments: [
    { name: "refund_amount", type: "number", unit: "cents, USD" },
    { name: "order_id", type: "str" },
    { name: "reason", type: "enum", values: ["damaged", "late"] },
    { name: "quantity", type: "int", min: 1, max: 10 },
    { name: "note", type: "string", required: false },
    { name: "priority", type: "custom_kind", min: 2 },
  ],
  returns: [
    { name: "refund_id", kind: "string" },
    { name: "amount", kind: "integer, cents, USD" },
    { name: "status" },
  ],
  tables: { recorded: true, read: ["orders"], changed: ["refunds", "orders"] },
};

const lookup = {
  tool: "lookup_order",
  description: "Finds an order.",
  effect: "read",
  reset_between_trials: false,
  arguments: [],
  returns: [],
  tables: { recorded: true, read: ["orders"], changed: [] },
};

const renderDrawer = (props = {}) =>
  render(<ToolDrawer tool={refund} onClose={vi.fn()} {...props} />);

describe("ToolDrawer", () => {
  it("renders nothing without a tool", () => {
    renderDrawer({ tool: null });
    expect(screen.queryByText("Arguments")).toBeNull();
  });

  it("names the tool and says what it does", () => {
    renderDrawer();
    expect(screen.getByText("issue_refund")).toBeInTheDocument();
    expect(screen.getByText("Refunds a charge.")).toBeInTheDocument();
  });

  it("describes each argument in plain words", () => {
    renderDrawer();
    expect(screen.getByText("Arguments")).toBeInTheDocument();
    expect(screen.getByText("number · cents, USD")).toBeInTheDocument();
    expect(screen.getByText("text")).toBeInTheDocument();
    expect(screen.getByText("one of damaged, late")).toBeInTheDocument();
    expect(screen.getByText("whole number · 1 to 10")).toBeInTheDocument();
    expect(screen.getByText("text · optional")).toBeInTheDocument();
    expect(screen.getByText("?")).toBeInTheDocument();
    expect(screen.getByText("custom_kind · at least 2")).toBeInTheDocument();
  });

  it("lists what the tool returns", () => {
    renderDrawer();
    expect(
      screen.getByText(
        "refund_id (string), amount (integer, cents, USD), status",
      ),
    ).toBeInTheDocument();
  });

  it("names the tables a writing tool changes and says it is reset", () => {
    renderDrawer();
    expect(screen.getByText("Changes")).toBeInTheDocument();
    expect(screen.getByText("refunds, orders")).toBeInTheDocument();
    expect(screen.queryByText("Reads")).toBeNull();
    expect(screen.getByText("Reset")).toBeInTheDocument();
    expect(
      screen.getByText("Restored to the seed before every trial"),
    ).toBeInTheDocument();
  });

  it("names the tables a reading tool reads and leaves the reset out", () => {
    renderDrawer({ tool: lookup });
    expect(screen.getByText("Reads")).toBeInTheDocument();
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.queryByText("Changes")).toBeNull();
    expect(screen.getAllByText("None")).toHaveLength(2);
    expect(screen.queryByText("Reset")).toBeNull();
  });

  it("says a tool touches none of the world's tables", () => {
    renderDrawer({
      tool: { ...lookup, tables: { recorded: false, read: [], changed: [] } },
    });
    expect(screen.getByText("None of the world's tables")).toBeInTheDocument();
  });

  it("says a write tool changes no table when it records none", () => {
    renderDrawer({
      tool: {
        ...lookup,
        effect: "write",
        tables: { recorded: true, read: ["orders"], changed: [] },
      },
    });
    expect(screen.getByText("Changes")).toBeInTheDocument();
    expect(screen.getByText("None of the world's tables")).toBeInTheDocument();
    expect(screen.queryByText("orders")).toBeNull();
  });

  it("shows the tool's source code with its file and lines", () => {
    renderDrawer({
      tool: {
        ...refund,
        entrypoint: {
          source: {
            path: "billing.py",
            line_start: 8,
            line_end: 9,
            code: "def refund(charge_id):\n    return ledger.refund(charge_id)",
          },
        },
      },
    });
    expect(screen.getByText("billing.py:8-9")).toBeInTheDocument();
    expect(screen.getByText(/ledger\.refund/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy code" })).toBeEnabled();
  });

  it("shows the source file alone when it has no line range", () => {
    renderDrawer({
      tool: {
        ...refund,
        entrypoint: { source: { path: "billing.py", code: "pass" } },
      },
    });
    expect(screen.getByText("billing.py")).toBeInTheDocument();
  });

  it("shows no code block when the build supplied no source", () => {
    renderDrawer({
      tool: { ...refund, entrypoint: { source: { path: "billing.py" } } },
    });
    expect(screen.queryByRole("button", { name: "Copy code" })).toBeNull();
    expect(screen.queryByText("billing.py")).toBeNull();
  });

  it("offers no Remove without a way to send it", () => {
    renderDrawer();
    expect(
      screen.queryByRole("button", { name: "Remove from the world" }),
    ).toBeNull();
  });

  it("hands the tool to Remove when it is allowed", () => {
    const onRemove = vi.fn();
    renderDrawer({ onRemove });
    fireEvent.click(
      screen.getByRole("button", { name: "Remove from the world" }),
    );
    expect(onRemove).toHaveBeenCalledWith(refund);
  });

  it("disables Remove while it is blocked", () => {
    const onRemove = vi.fn();
    renderDrawer({ onRemove, blockedReason: "The builder is busy." });
    expect(
      screen.getByRole("button", { name: "Remove from the world" }),
    ).toBeDisabled();
  });
});
