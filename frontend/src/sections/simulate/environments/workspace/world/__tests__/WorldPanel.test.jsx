import { afterEach, describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { enqueueSnackbar } from "notistack";
import { render, screen, within } from "src/utils/test-utils";
import useSimulationAccess from "src/sections/simulate/environments/hooks/useSimulationAccess";
import WorldPanel from "../WorldPanel";
import { toolRemovalInstruction } from "../../contract/contractInstructions";
import { CONTRACT_EDIT_COPY } from "../../contract/contract.constants";

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

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
      effect: "write",
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
  chat = null,
) => render(<WorldPanel env={env} envState={envState} chat={chat} />);

const tableRow = (name) => screen.getByText(name).closest('[role="button"]');

const LOOKUP_CHIP = "Finds an order. · reads this table";
const REFUND_CHIP = "Refunds a charge. · changes this table";

afterEach(() => {
  vi.mocked(useSimulationAccess).mockReturnValue({
    canEdit: true,
    canRun: true,
  });
  vi.mocked(enqueueSnackbar).mockClear();
});

describe("WorldPanel", () => {
  it("says how many tools the agent calls and how many the world answers", () => {
    renderPanel();
    expect(
      screen.getByText(
        "Your agent calls 3 tools; the world answers 2 of them.",
      ),
    ).toBeInTheDocument();
  });

  it("says the world answers every tool when all are simulated", () => {
    renderPanel({
      ...builtEnv,
      toolStatus: builtEnv.toolStatus.slice(0, 2),
    });
    expect(
      screen.getByText(
        "Your agent calls 2 tools; the world answers every one.",
      ),
    ).toBeInTheDocument();
  });

  it("names the agent and its version in the summary", () => {
    renderPanel({
      ...builtEnv,
      testSubject: { name: "Refund bot", active_version: "v3" },
    });
    expect(
      screen.getByText(
        "Agent Refund bot v3 calls 3 tools; the world answers 2 of them.",
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

  it("marks a seeded world as simulated, and a connect-only one not", () => {
    const { unmount } = renderPanel();
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    unmount();
    renderPanel({
      detailReady: true,
      tools: [{ name: "book_ride" }],
      worldTables: [],
      stores: [],
      toolStatus: [status("book_ride", "in_process")],
    });
    expect(screen.queryByText("Simulated")).toBeNull();
  });

  it("maps each table with its rows, columns and the tools that use it", () => {
    renderPanel();
    expect(screen.getByText("World · 2 tables · 3 tools")).toBeInTheDocument();
    expect(
      screen.getByText(
        "4 rows. Each table with the tools that use it; a tool opens its details.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Tools that use it")).toBeInTheDocument();
    expect(screen.getByText("world_db · postgres")).toBeInTheDocument();
    expect(screen.queryByText("No tool uses it")).toBeNull();

    const orders = within(tableRow("orders"));
    expect(orders.getByText("3")).toBeInTheDocument();
    expect(orders.getByText("2")).toBeInTheDocument();
    expect(orders.getByText("lookup_order")).toBeInTheDocument();
    expect(orders.queryByText("issue_refund")).toBeNull();

    const refunds = within(tableRow("refunds"));
    expect(refunds.getByText("issue_refund")).toBeInTheDocument();
    expect(refunds.getByText("0")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: LOOKUP_CHIP })).toBeVisible();
    expect(screen.getByRole("button", { name: REFUND_CHIP })).toBeVisible();

    const none = screen.getByText("No table").parentElement;
    expect(within(none).getByText("summarise")).toBeInTheDocument();
  });

  it("adds the row counts when the build reported no total", () => {
    renderPanel({ ...builtEnv, worldTotalRows: undefined });
    expect(
      screen.getByText(
        "4 rows. Each table with the tools that use it; a tool opens its details.",
      ),
    ).toBeInTheDocument();
  });

  it("opens and folds a table's columns", async () => {
    const user = userEvent.setup();
    renderPanel();
    const orders = tableRow("orders");
    expect(orders).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("order_id")).toBeNull();

    await user.click(orders);
    expect(orders).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("order_id")).toBeInTheDocument();
    expect(screen.getByText("float")).toBeInTheDocument();

    await user.click(orders);
    expect(screen.queryByText("order_id")).toBeNull();

    orders.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByText("order_id")).toBeInTheDocument();
  });

  it("does not fold a table open when one of its tools is clicked", async () => {
    const user = userEvent.setup();
    renderPanel();
    const orders = tableRow("orders");
    await user.click(screen.getByRole("button", { name: LOOKUP_CHIP }));
    expect(screen.getByText("Finds an order.")).toBeInTheDocument();
    expect(orders).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("order_id")).toBeNull();
  });

  it("puts the tools that use no table in a row of their own", () => {
    renderPanel({
      ...builtEnv,
      toolStatus: builtEnv.toolStatus.map((t) => ({
        ...t,
        tables: { recorded: false, read: [], changed: [], label: NOT_RECORDED },
      })),
    });
    expect(screen.getAllByText("No tool uses it")).toHaveLength(2);
    const none = within(screen.getByText("No table").parentElement);
    expect(none.getByText("lookup_order")).toBeInTheDocument();
    expect(none.getByText("issue_refund")).toBeInTheDocument();
    expect(none.getByText("summarise")).toBeInTheDocument();
  });

  it("counts the tools in each tier above the tables", () => {
    renderPanel();
    expect(screen.getByText("2 tools: Simulated")).toBeInTheDocument();
    expect(
      screen.getByText("1 tool: Runs inside the agent"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Unsupported/)).toBeNull();
  });

  it("says all tools share a tier when they do", () => {
    renderPanel({ ...builtEnv, toolStatus: builtEnv.toolStatus.slice(0, 2) });
    expect(screen.getByText("All tools: Simulated")).toBeInTheDocument();
  });

  it("lists the tools by where they run when the world has no tables", () => {
    renderPanel({ ...builtEnv, worldTables: [] });
    expect(screen.getByText("Tools · 3")).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Simulated" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Runs inside the agent" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Unsupported" })).toBeNull();
    expect(screen.queryByText(/^World · /)).toBeNull();
  });

  it("opens a tool's details from the World card and closes them", async () => {
    const user = userEvent.setup();
    renderPanel();
    expect(screen.queryByText("Finds an order.")).toBeNull();

    await user.click(screen.getByRole("button", { name: LOOKUP_CHIP }));
    expect(screen.getByText("Finds an order.")).toBeInTheDocument();
    expect(screen.getByText("Reads")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByText("Finds an order.")).toBeNull();
  });

  it("opens a tool's details from the tools list", async () => {
    const user = userEvent.setup();
    renderPanel({ ...builtEnv, worldTables: [] });
    await user.click(
      screen.getByRole("button", { name: "Show the contract of issue_refund" }),
    );
    expect(screen.getByText("Changes")).toBeInTheDocument();
    expect(screen.getByText("refunds")).toBeInTheDocument();
  });

  it("offers no Remove without a builder chat", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: REFUND_CHIP }));
    expect(
      screen.queryByRole("button", { name: "Remove from the world" }),
    ).toBeNull();
  });

  it("asks the builder to remove a tool once confirmed", async () => {
    const user = userEvent.setup();
    const chat = { send: vi.fn(), live: true, busy: false };
    renderPanel(builtEnv, undefined, chat);
    await user.click(screen.getByRole("button", { name: REFUND_CHIP }));
    await user.click(
      screen.getByRole("button", { name: "Remove from the world" }),
    );
    expect(screen.getByText("Remove issue_refund?")).toBeInTheDocument();
    expect(chat.send).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Remove tool" }));
    expect(chat.send).toHaveBeenCalledTimes(1);
    expect(chat.send).toHaveBeenCalledWith(
      toolRemovalInstruction("issue_refund"),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(CONTRACT_EDIT_COPY.sent, {
      variant: "info",
    });
    expect(screen.queryByText("Refunds a charge.")).toBeNull();
  });

  it("sends nothing when the removal is cancelled", async () => {
    const user = userEvent.setup();
    const chat = { send: vi.fn(), live: true, busy: false };
    renderPanel(builtEnv, undefined, chat);
    await user.click(screen.getByRole("button", { name: REFUND_CHIP }));
    await user.click(
      screen.getByRole("button", { name: "Remove from the world" }),
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(chat.send).not.toHaveBeenCalled();
    expect(screen.getByText("Refunds a charge.")).toBeInTheDocument();
  });

  it.each([
    ["the builder is not live", { live: false, busy: false }, true],
    ["the builder is busy", { live: true, busy: true }, true],
    ["the viewer cannot edit", { live: true, busy: false }, false],
  ])("blocks Remove when %s", async (_, state, canEdit) => {
    vi.mocked(useSimulationAccess).mockReturnValue({ canEdit, canRun: true });
    const user = userEvent.setup();
    renderPanel(builtEnv, undefined, { send: vi.fn(), ...state });
    await user.click(screen.getByRole("button", { name: REFUND_CHIP }));
    expect(
      screen.getByRole("button", { name: "Remove from the world" }),
    ).toBeDisabled();
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
