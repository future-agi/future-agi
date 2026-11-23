import { describe, it, expect } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
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

const openTool = (user, name) =>
  user.click(
    screen.getByRole("button", { name: `Show the contract of ${name}` }),
  );

describe("WorldPanel contract evidence", () => {
  it("marks each tool with its reported effect and check, and shows why", async () => {
    const user = userEvent.setup();
    renderPanel({
      tools: [
        {
          name: "lookup_order",
          desc: "Reads one order.",
          effectKind: "read",
          effectEvidence: {
            origin: { kind: "file", path: "tools.py", line: 3 },
            excerpt: "SELECT * FROM orders",
          },
          certification: {
            availability: "runtime_only",
            probe_mode: "runtime_only",
          },
        },
        {
          name: "issue_refund",
          desc: "Refunds a charge.",
          effectKind: "write",
        },
      ],
    });

    expect(screen.getByText("Runtime only")).toBeInTheDocument();
    await openTool(user, "lookup_order");
    expect(screen.getByText("Reads data")).toBeInTheDocument();
    expect(screen.getByText("Why: reads data")).toBeInTheDocument();
    expect(screen.getByText("SELECT * FROM orders")).toBeInTheDocument();
    expect(screen.getByText("tools.py")).toBeInTheDocument();
  });

  it("shows no reported effect or check for a tool the build did not classify", async () => {
    const user = userEvent.setup();
    renderPanel();

    expect(screen.queryByText("Runtime only")).toBeNull();
    await openTool(user, "lookup_order");
    expect(screen.queryByText("Reported effect")).toBeNull();
    expect(screen.queryByText("Checked")).toBeNull();
  });

  it("shows where each tool was declared", () => {
    renderPanel({
      tools: [
        {
          name: "lookup_order",
          origin: { kind: "code", path: "support/tools.py", line: 18 },
        },
        {
          name: "issue_refund",
          origin: { kind: "provider_tool_list", field: "issue_refund" },
        },
      ],
    });

    expect(screen.getByText("Code")).toBeInTheDocument();
    expect(screen.getByText("Tool list")).toBeInTheDocument();
  });

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

    await openTool(user, "issue_refund");
    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("billing.py:8-9")).toBeInTheDocument();
    expect(screen.getByText(/ledger\.refund/)).toBeInTheDocument();
  });

  it("shows a provider function's address and parameters", async () => {
    const user = userEvent.setup();
    renderPanel({
      toolEntrypoints: [
        {
          tool: "lookup_order",
          provider_function: {
            url: "https://tools.example.test/orders",
            parameters: {
              type: "object",
              properties: { order_id: { type: "string" } },
            },
          },
        },
      ],
    });

    await openTool(user, "lookup_order");
    expect(screen.getByText("Provider function")).toBeInTheDocument();
    expect(
      screen.getByText("https://tools.example.test/orders"),
    ).toBeInTheDocument();
    expect(screen.getByText(/"order_id"/)).toBeInTheDocument();
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

    await user.click(
      screen.getByRole("button", { name: "Show the columns of orders" }),
    );
    expect(screen.getByText("UUID · PK · NOT NULL")).toBeInTheDocument();
    expect(
      screen.getByText("TEXT · references customers.id"),
    ).toBeInTheDocument();
    expect(screen.queryByText("str")).toBeNull();
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
