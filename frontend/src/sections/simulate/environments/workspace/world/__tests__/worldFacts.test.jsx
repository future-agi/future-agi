import { describe, it, expect } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";
import WorldPanel from "../WorldPanel";

const status = (tool, extra = {}) => ({
  tool,
  status: "simulated",
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
  tables: { recorded: false, read: [], changed: [], label: "" },
  ...extra,
});

const baseEnv = {
  detailReady: true,
  tools: [
    { name: "lookup_order", desc: "Reads one order." },
    { name: "issue_refund", desc: "Refunds a charge." },
  ],
  worldTables: [
    {
      name: "orders",
      rows: 8,
      note: "world_db · postgres",
      fields: [{ name: "id", type: "str" }],
    },
  ],
  worldTotalRows: 8,
  stores: [{ capability: "world_db", engine: "postgres", tables: [] }],
  toolStatus: [
    status("lookup_order", { description: "Reads one order." }),
    status("issue_refund", { description: "Refunds a charge." }),
  ],
};

const renderPanel = (env = {}) =>
  render(
    <WorldPanel
      env={{ ...baseEnv, ...env }}
      envState={{ agent: { typeId: "vapi" } }}
    />,
  );

const openTool = (user, description) =>
  user.click(
    screen.getByRole("button", { name: `${description} · reads this table` }),
  );

describe("WorldPanel contract evidence", () => {
  it("shows the exact tool source when the build supplied it", async () => {
    const user = userEvent.setup();
    renderPanel({
      toolEntrypoints: [
        {
          tool: "issue_refund",
          module: "billing",
          callable: "refund",
          source: {
            path: "billing.py",
            line_start: 8,
            line_end: 9,
            code: "def refund(charge_id):\n    return ledger.refund(charge_id)",
          },
        },
      ],
    });

    await openTool(user, "Refunds a charge.");
    expect(screen.getByText("billing.py:8-9")).toBeInTheDocument();
    expect(screen.getByText(/ledger\.refund/)).toBeInTheDocument();
  });

  it("shows no source for a tool the build supplied none for", async () => {
    const user = userEvent.setup();
    renderPanel({
      toolEntrypoints: [
        {
          tool: "issue_refund",
          source: { path: "billing.py", line_start: 8, line_end: 9, code: "x" },
        },
      ],
    });

    await openTool(user, "Reads one order.");
    expect(screen.queryByText("billing.py:8-9")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy code" })).toBeNull();
  });

  it("types each column from the source schema", async () => {
    const user = userEvent.setup();
    renderPanel({
      dataSchemaColumns: {
        orders: [
          { name: "event_id", type: "UUID", pk: true, nullable: false },
          { name: "customer_id", type: "TEXT", references: "customers.id" },
        ],
      },
    });

    const orders = screen.getByText("orders").closest('[role="button"]');
    expect(within(orders).getByText("2")).toBeInTheDocument();
    await user.click(orders);
    expect(screen.getByText("event_id")).toBeInTheDocument();
    expect(screen.getByText("UUID")).toBeInTheDocument();
    expect(screen.getByText("TEXT")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Primary key")).toHaveLength(1);
    expect(screen.queryByText("str")).toBeNull();
  });

  it("builds the tables from the source schema when the world lists none", () => {
    renderPanel({
      worldTables: [],
      worldTotalRows: undefined,
      dataSchemaColumns: {
        orders: [{ name: "id", type: "UUID", pk: true }],
        customers: [{ name: "id", type: "UUID" }],
        broken: "not a column list",
      },
      baseEnvironment: { orders: [{ id: 1 }, { id: 2 }] },
    });

    expect(screen.getByText("World · 2 tables · 2 tools")).toBeInTheDocument();
    expect(
      screen.getByText(
        "2 rows. Each table with the tools that use it; a tool opens its details.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("customers")).toBeInTheDocument();
    expect(screen.queryByText("broken")).toBeNull();
    const orders = screen.getByText("orders").closest('[role="button"]');
    expect(within(orders).getByText("2")).toBeInTheDocument();
  });

  it("shows the data store, the refusal and the runtime", async () => {
    const user = userEvent.setup();
    renderPanel({
      dataStore: {
        kind: "postgres",
        host: "support-db",
        password: "must-not-render",
      },
      refusalSignature: "Order not found",
      runtime: { readiness: { path: "/health" } },
    });

    expect(screen.getByText("support-db")).toBeInTheDocument();
    expect(screen.getByText("••••••")).toBeInTheDocument();
    expect(screen.queryByText("must-not-render")).toBeNull();
    expect(
      screen.getByText("When nothing matches, the agent hears"),
    ).toBeInTheDocument();
    expect(screen.getByText("Order not found")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show the runtime" }));
    expect(screen.getByText(/"path": "\/health"/)).toBeInTheDocument();
  });

  it("hides the password source for a store that lives inside the agent", () => {
    const { unmount } = renderPanel({
      dataStore: {
        kind: "in_process",
        configured_by: "general_prompt",
        password_from: "[redacted]",
      },
    });
    expect(screen.getByText("in_process")).toBeInTheDocument();
    expect(screen.queryByText("password from")).toBeNull();
    expect(screen.queryByText("[redacted]")).toBeNull();
    unmount();

    renderPanel({
      dataStore: { kind: "postgres", password_from: "DB_PASSWORD" },
    });
    expect(screen.getByText("password from")).toBeInTheDocument();
    expect(screen.queryByText("DB_PASSWORD")).toBeNull();
    expect(screen.getByText("••••••")).toBeInTheDocument();
  });

  it("shows no data store card when the build reported none", () => {
    renderPanel();
    expect(screen.queryByText("Data store")).toBeNull();
  });
});
