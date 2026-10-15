import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import RlContractPanel from "../RlContractPanel";

const baseEnv = {
  name: "Refund Copilot",
  surface: "voice",
  tools: [
    { name: "lookup_order", desc: "Reads one order." },
    { name: "issue_refund", desc: "Refunds a charge." },
  ],
  seed: { tables: [{ name: "orders", rows: 8 }] },
  endConditions: {
    terminate_reasons: ["assistant-ended-call", "customer-ended-call"],
    truncate_reasons: ["cancelled", "max-duration-reached"],
    max_turns: 12,
    clock: "real-time",
    seed: { value: 7, controls: ["scenario_world_rng"] },
  },
};

const renderPanel = (env = {}) =>
  render(
    <RlContractPanel
      env={{ ...baseEnv, ...env }}
      envState={{ evals: [], agent: { name: "Support agent" } }}
      patch={vi.fn()}
    />,
  );

const column = (label) => screen.getByText(label).parentElement;

describe("RlContractPanel contract facts", () => {
  it("puts each end reason in its own column and keeps the limits under truncate", () => {
    renderPanel();

    const terminate = within(column("Terminate"));
    const truncate = within(column("Truncate"));
    expect(terminate.getByText("assistant-ended-call")).toBeInTheDocument();
    expect(terminate.getByText("customer-ended-call")).toBeInTheDocument();
    expect(terminate.queryByText("cancelled")).toBeNull();
    expect(truncate.getByText("cancelled")).toBeInTheDocument();
    expect(truncate.getByText("max-duration-reached")).toBeInTheDocument();
    expect(truncate.getByText("12 turns")).toBeInTheDocument();
  });

  // it("shows the real seed and what it fixes, or says no seed was set", () => {
  //   const { unmount } = renderPanel();
  //   expect(screen.getByText("Deterministic seed · 7")).toBeInTheDocument();
  //   expect(screen.getByText(/^Fixes scenario world rng\./)).toBeInTheDocument();
  //   unmount();

  //   renderPanel({
  //     endConditions: { ...baseEnv.endConditions, seed: undefined },
  //   });
  //   expect(screen.getByText("Deterministic seed")).toBeInTheDocument();
  //   expect(
  //     screen.getByText(/No seed was set for this environment/),
  //   ).toBeInTheDocument();
  // });

  it("marks each tool with its effect and availability, and shows the effect evidence", () => {
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

    expect(screen.getByText("reads")).toBeInTheDocument();
    expect(screen.getByText("writes")).toBeInTheDocument();
    expect(screen.getByText("runtime only")).toBeInTheDocument();

    fireEvent.click(screen.getAllByText("lookup_order")[0]);
    expect(screen.getByText("Why it reads")).toBeInTheDocument();
    expect(screen.getByText("SELECT * FROM orders")).toBeInTheDocument();
  });

  it("shows no effect or availability for a tool ALK did not classify", () => {
    renderPanel();

    expect(screen.queryByText("reads")).toBeNull();
    expect(screen.queryByText("writes")).toBeNull();
    expect(screen.queryByText("runtime only")).toBeNull();
  });

  it("shows the refusal, the starting data and the runtime on the database card", () => {
    renderPanel({
      refusalSignature: "Order not found",
      baseEnvironment: { orders: [{ id: "order-1" }] },
      runtime: { readiness: { path: "/health" } },
    });

    expect(
      screen.getByText("When nothing matches, the agent hears"),
    ).toBeInTheDocument();
    expect(screen.getByText("Order not found")).toBeInTheDocument();

    fireEvent.click(screen.getAllByText("Starting data")[0]);
    expect(screen.getByText(/"id": "order-1"/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByText("Runtime")[0]);
    expect(screen.getByText(/"path": "\/health"/)).toBeInTheDocument();
  });

  it("counts a table's rows from the starting data when the sandbox reported none", () => {
    renderPanel({
      seed: { tables: [] },
      dataSchema: { patients: { id: "string" } },
      baseEnvironment: { patients: [{ id: "p1" }, { id: "p2" }, { id: "p3" }] },
    });

    expect(screen.getByText("3 rows · starting data")).toBeInTheDocument();
  });

  it("prefers the sandbox's own row count over the starting data", () => {
    renderPanel({
      seed: { tables: [{ name: "orders", rows: 8 }] },
      baseEnvironment: { orders: [{ id: "o1" }] },
    });

    expect(screen.getByText("8 rows")).toBeInTheDocument();
    expect(screen.queryByText(/starting data$/)).toBeNull();
  });

  it("lists the properties of a table written as JSON Schema", () => {
    renderPanel({
      seed: { tables: [] },
      dataSchema: {
        orders: {
          type: "object",
          description: "Food orders",
          properties: {
            order_id: { type: "string", description: "Order id" },
            minutes_late: { type: "integer" },
          },
        },
      },
    });

    fireEvent.click(screen.getByText("orders"));
    expect(screen.getByText("order_id")).toBeInTheDocument();
    expect(screen.getByText("minutes_late")).toBeInTheDocument();
    expect(screen.getByText("integer")).toBeInTheDocument();
    expect(screen.queryByText("properties")).toBeNull();
    expect(screen.queryByText("[object Object]")).toBeNull();
  });

  it("hides the password source for a store that lives inside the agent", () => {
    const { unmount } = renderPanel({
      dataStore: { kind: "in_process", configured_by: "general_prompt", password_from: "[redacted]" },
    });
    expect(screen.getByText("in_process")).toBeInTheDocument();
    expect(screen.queryByText("password from")).toBeNull();
    expect(screen.queryByText("[redacted]")).toBeNull();
    unmount();

    renderPanel({ dataStore: { kind: "postgres", password_from: "DB_PASSWORD" } });
    expect(screen.getByText("password from")).toBeInTheDocument();
    expect(screen.queryByText("DB_PASSWORD")).toBeNull();
    expect(screen.getByText("••••••")).toBeInTheDocument();
  });

  it("lists the actors with their role and origin", () => {
    renderPanel({
      actors: [
        {
          name: "supervisor",
          role: "approves refunds over $200",
          origin: { kind: "system_prompt", field: "general_prompt" },
        },
      ],
    });

    expect(screen.getByText("Actors")).toBeInTheDocument();
    expect(screen.getByText("supervisor")).toBeInTheDocument();
    expect(screen.getByText("approves refunds over $200")).toBeInTheDocument();
    expect(screen.getByText("SYSTEM PROMPT")).toBeInTheDocument();
  });

  it("lists the sub-goals with their kind, output and overlay", () => {
    renderPanel({
      subGoals: [
        {
          name: "identity_checked",
          what: "Identity was checked",
          kind: "judge",
          output: "pass_fail",
          overlay: "authentication",
        },
      ],
    });

    expect(screen.getByText("Sub-goals")).toBeInTheDocument();
    expect(screen.getByText("identity_checked")).toBeInTheDocument();
    expect(screen.getByText("judge")).toBeInTheDocument();
    expect(screen.getByText("pass/fail")).toBeInTheDocument();
    expect(screen.getByText("authentication")).toBeInTheDocument();
  });

  it("shows no actors or sub-goals card when the contract carries none", () => {
    renderPanel();

    expect(screen.queryByText("Actors")).toBeNull();
    expect(screen.queryByText("Sub-goals")).toBeNull();
  });
});
