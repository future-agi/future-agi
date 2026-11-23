import { describe, it, expect } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";
import WorldPanel from "../WorldPanel";

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

const builtEnv = {
  detailReady: true,
  tools: [{ name: "lookup_order" }, { name: "issue_refund" }],
  source: { kind: "github", repository: "acme/refund-agent", ref: "main" },
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
  stores: [{ capability: "world_db", engine: "postgres", tables: [] }],
  toolStatus: [
    status("lookup_order", "simulated", {
      description: "Finds an order.",
      tables: {
        recorded: true,
        read: ["orders"],
        changed: [],
        label: "Reads orders; changes no table",
      },
    }),
    status("issue_refund", "simulated", {
      description: "Refunds a charge.",
      tables: {
        recorded: true,
        read: ["orders"],
        changed: ["refunds"],
        label: "Reads orders; changes refunds",
      },
    }),
    status("summarise", "in_process", { description: "Summarises the call." }),
  ],
};

const renderPanel = (
  env = builtEnv,
  envState = { agent: { typeId: "vapi" } },
) => render(<WorldPanel env={env} envState={envState} />);

describe("WorldPanel", () => {
  it("says how many tools the agent calls and how many the test world answers", () => {
    renderPanel();
    expect(
      screen.getByText(
        "Your agent calls 3 tools. The test world answers 2 of them.",
      ),
    ).toBeInTheDocument();
  });

  it("says the test world answers every tool when all are simulated", () => {
    renderPanel({
      ...builtEnv,
      toolStatus: builtEnv.toolStatus.slice(0, 2),
    });
    expect(
      screen.getByText(
        "Your agent calls 2 tools, and the test world answers every one.",
      ),
    ).toBeInTheDocument();
  });

  it("names the repository and ref the world was read from", () => {
    renderPanel();
    expect(screen.getByText("refund-agent @ main")).toBeInTheDocument();
  });

  it("shows no origin when the environment was not read from a repository", () => {
    renderPanel({ ...builtEnv, source: { kind: "vapi" } });
    expect(screen.queryByText(/ @ /)).toBeNull();
  });

  it("maps each table with its rows, columns and the tools that use it", async () => {
    const user = userEvent.setup();
    renderPanel();
    expect(screen.getByText("World · 2 tables · 3 tools")).toBeInTheDocument();
    expect(
      screen.getByText("4 rows. Each table with the tools that use it."),
    ).toBeInTheDocument();
    expect(screen.getByText("Tools that use it")).toBeInTheDocument();
    expect(screen.getByText("world_db · postgres")).toBeInTheDocument();
    expect(screen.queryByText("No tool uses it")).toBeNull();

    const orders = screen.getByRole("button", {
      name: "Show the columns of orders",
    });
    expect(within(orders).getByText("lookup_order")).toBeInTheDocument();
    expect(within(orders).getByText("issue_refund")).toBeInTheDocument();
    expect(within(orders).getByText("2")).toBeInTheDocument();

    await user.click(orders);
    expect(screen.getByText("order_id")).toBeInTheDocument();
    expect(screen.getByText("float")).toBeInTheDocument();
  });

  it("leaves the tools column out when no tool records its tables", () => {
    renderPanel({
      ...builtEnv,
      toolStatus: builtEnv.toolStatus.map((t) => ({
        ...t,
        tables: { recorded: false, read: [], changed: [], label: NOT_RECORDED },
      })),
    });
    expect(screen.queryByText("Tools that use it")).toBeNull();
    expect(
      screen.getByText("4 rows seeded before each run."),
    ).toBeInTheDocument();
  });

  it("groups the tools by where they run", () => {
    renderPanel();
    expect(screen.getByText("Tools · 3")).toBeInTheDocument();
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    expect(screen.getByText("Runs inside the agent")).toBeInTheDocument();
    expect(screen.queryByText("Unsupported")).toBeNull();
  });

  it("keeps the capability graph folded until asked for", () => {
    renderPanel();
    expect(
      screen.getByRole("button", { name: /Capability graph/ }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("shows the no-world card for an environment with no tables and no stores", () => {
    renderPanel({
      detailReady: true,
      tools: [{ name: "book_ride" }],
      worldTables: [],
      stores: [],
      toolStatus: [status("book_ride", "in_process")],
    });
    expect(screen.getByText("No world to reset")).toBeInTheDocument();
    expect(
      screen.getByText("book_ride", { selector: "span" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No tables or stores are seeded for this environment."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^World · /)).toBeNull();
  });

  it("does not call an environment without detail yet a no-world one", () => {
    renderPanel({ tools: [{ name: "book_ride" }] });
    expect(screen.queryByText("No world to reset")).toBeNull();
  });

  it("shows none of the sample-only parts", () => {
    renderPanel();
    expect(screen.queryByText("Try the tools yourself")).toBeNull();
    expect(screen.queryByText("Add tools")).toBeNull();
    expect(screen.queryByText(/Fixed|Recorded|Live/)).toBeNull();
    expect(screen.queryByText(/Scenario checks/)).toBeNull();
  });
});
