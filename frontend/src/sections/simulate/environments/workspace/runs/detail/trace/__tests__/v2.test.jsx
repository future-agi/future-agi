import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const useRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useRunCalls: (...args) => useRunCalls(...args),
}));

vi.mock(
  "src/api/simulate-environments/environments",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useEnvironmentRunTest: () => ({
      data: [{ id: "eval-1", regradable: true, editable: true }],
      isPending: false,
      isError: false,
    }),
  }),
);

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => enqueueSnackbar(...args),
}));

const { default: TraceTable } = await import("../TraceTable");
const { default: RunTraceTable } = await import("../RunTraceTable");
const { default: RerunSelectionBar } = await import("../RerunSelectionBar");
const { default: EvalColumnActions } = await import("../../EvalColumnActions");
const { mapCallRow } = await import("src/api/simulate-environments/runCalls");

const call = (id, overrides = {}) => ({
  id,
  scenario: `Scenario ${id}`,
  goal: "Refund a double charge",
  status: "passed",
  executionStatus: "completed",
  csat: 8,
  turns: 5,
  latencyMs: 300,
  tokens: 200,
  durationMs: 40000,
  sourceScenarioKey: `key-${id}`,
  personaDetails: {
    name: "Priya",
    voice: "Indian female",
    age: "34",
    traits: ["impatient", "in a hurry"],
  },
  subGoalResults: [],
  evalResults: [{ id: "eval-1", name: "Tone", score: 0.9, passed: true }],
  ...overrides,
});
const group = (label, rows, overrides = {}) => ({
  label,
  rows,
  count: rows.length,
  measured: rows.length,
  passed: rows.filter((r) => r.status === "passed").length,
  agg: {},
  ...overrides,
});
const EVALS = [{ id: "eval-1", name: "Tone" }];

const renderTable = (props = {}) =>
  render(
    <TraceTable
      groups={[group("Refunds", [call("c1")])]}
      evals={EVALS}
      onOpen={vi.fn()}
      groupView={{ all: true, expanded: new Set() }}
      onGroupViewChange={vi.fn()}
      {...props}
    />,
  );
const callRow = (id) => screen.getByText(`Scenario ${id}`).closest("tr");

const STATUS_COUNTS = {
  queued: 0,
  in_progress: 0,
  passed: 3,
  failed: 2,
  error: 1,
  inconclusive: 0,
};
const calls = (opts = {}) => {
  const tasks = [call("c1"), call("c2", { status: "failed" })];
  return {
    tasks,
    columns: [{ key: "eval-1", label: "Tone", group: "Evaluations" }],
    groups: opts.groupBy ? [group("Refunds", tasks)] : [],
    facets: {},
    statusCounts: STATUS_COUNTS,
    count: tasks.length,
    totalPages: 1,
    isLoading: false,
  };
};
const renderRunTable = (props = {}) =>
  render(<RunTraceTable executionId="ex1" onOpenCall={vi.fn()} {...props} />);

beforeEach(() => {
  enqueueSnackbar.mockReset();
  useRunCalls.mockReset();
  useRunCalls.mockImplementation((_id, opts) => calls(opts));
});

describe("run detail table, Vel's layout", () => {
  it('group row reads "<label> · N scenarios · P% passed p/m"', () => {
    const rows = [call("c1"), call("c2"), call("c3", { status: "failed" })];
    const { unmount } = renderTable({
      groups: [group("Refunds", rows, { count: 6, measured: 5, passed: 4 })],
      trials: 3,
    });
    const label = screen.getByText("Refunds").closest("td");
    expect(label).toHaveTextContent("Refunds · 2 scenarios · 80% passed 4/5");
    unmount();

    // One repeat: every call is its own scenario.
    renderTable({
      groups: [group("Refunds", rows, { count: 3, measured: 0, passed: 0 })],
    });
    const single = screen.getByText("Refunds").closest("td");
    expect(single).toHaveTextContent("Refunds · 3 scenarios");
    // Nothing scored yet: no pass rate to read.
    expect(single).not.toHaveTextContent("passed");
  });

  it("group row counts calls when a scenario's repeats split across groups", () => {
    renderTable({
      groups: [group("Failed", [call("c1")], { count: 4, measured: 4 })],
      trials: 3,
      trialsSplit: true,
    });
    expect(screen.getByText("Failed").closest("td")).toHaveTextContent(
      "Failed · 4 calls",
    );
  });

  it("clicking an eval score opens the call on that eval", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    renderTable({ onOpen });

    await user.click(screen.getByText("90%"));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "c1" }), {
      evalId: "eval-1",
    });

    // Anywhere else on the row opens the call itself.
    onOpen.mockClear();
    await user.click(screen.getByText("Scenario c1"));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "c1" }));
    expect(onOpen.mock.calls[0]).toHaveLength(1);
  });

  it('sub-goals read "Failed · 4 of 6 met", missed first, Show all', async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    renderTable({
      onOpen,
      groups: [
        group("Refunds", [
          call("c1", {
            status: "failed",
            subGoalResults: [
              { name: "Greet", passed: true },
              { name: "Verify identity", passed: true },
              { name: "Create refund", passed: false },
              { name: "Confirm amount", passed: true },
              { name: "Send receipt", passed: null },
              { name: "Close politely", passed: true },
            ],
          }),
        ]),
      ],
    });
    const row = callRow("c1");
    expect(row).toHaveTextContent("Failed · 4 of 6 met");
    // Only what wasn't met shows until Show all.
    expect(within(row).getByText("Create refund")).toBeInTheDocument();
    expect(within(row).getByText("Send receipt")).toBeInTheDocument();
    expect(within(row).queryByText("Greet")).toBeNull();

    await user.click(within(row).getByRole("button", { name: "Show all" }));
    expect(within(row).getByText("Greet")).toBeInTheDocument();
    expect(
      within(within(row).getByText("Greet").parentElement).getByRole("img", {
        name: "Passed",
      }),
    ).toBeInTheDocument();
    // Show all works the list, not the row.
    expect(onOpen).not.toHaveBeenCalled();
    expect(
      within(row).getByRole("button", { name: "Hide" }),
    ).toBeInTheDocument();
  });

  it("sub-goals read Passed when every one was met", () => {
    renderTable({
      groups: [
        group("Refunds", [
          call("c1", {
            subGoalResults: [
              { name: "Greet", passed: true },
              { name: "Verify identity", passed: true },
            ],
          }),
        ]),
      ],
    });
    expect(callRow("c1")).toHaveTextContent("Passed · 2 of 2 met");
    expect(within(callRow("c1")).queryByText("Greet")).toBeNull();
  });

  it("persona is one line with the full card on hover", async () => {
    const user = userEvent.setup();
    renderTable();
    const cell = within(callRow("c1")).getByText("Priya").closest("td");
    expect(cell).toHaveTextContent("Priya");
    expect(cell).toHaveTextContent("34 · Indian female");
    // Traits only on the card.
    expect(cell).not.toHaveTextContent("impatient");

    await user.hover(within(cell).getByText("Priya"));
    const card = await screen.findByRole("tooltip");
    expect(card).toHaveTextContent("Name:Priya");
    expect(card).toHaveTextContent("Voice:Indian female");
    expect(card).toHaveTextContent("Age:34");
    expect(card).toHaveTextContent("Traits:impatient, in a hurry");
  });

  it("persona shows a dash with no persona", () => {
    renderTable({
      groups: [
        group("Refunds", [
          call("c1", {
            personaDetails: { name: null, voice: null, age: null, traits: [] },
          }),
        ]),
      ],
      columns: new Set(["callDetails", "persona"]),
    });
    const cells = callRow("c1").querySelectorAll("td");
    expect(cells[cells.length - 2]).toHaveTextContent(/^-$/);
  });

  it("red CSAT and missing latency explain themselves", async () => {
    const user = userEvent.setup();
    renderTable({
      groups: [group("Refunds", [call("c1", { csat: 3, latencyMs: null })])],
      columns: new Set(["callDetails", "csat", "latency"]),
    });
    const [, csatCell, latencyCell] = callRow("c1").querySelectorAll("td");

    await user.hover(within(csatCell).getByText("3"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "CSAT 3 of 10. 4 or below is an unhappy customer.",
    );
    await user.unhover(within(csatCell).getByText("3"));
    await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());

    await user.hover(within(latencyCell).getByText("-"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "No response time was reported.",
    );
  });

  it("Enter on a focused row opens it", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    renderTable({ onOpen });

    callRow("c1").focus();
    expect(callRow("c1")).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "c1" }));
  });

  it("Scenario column is off by default; extra columns are in the picker", async () => {
    const user = userEvent.setup();
    renderRunTable();

    for (const name of [
      "Scenario",
      "Situation",
      "Sub-goals",
      "Stop latency",
      "AI interruptions",
    ])
      expect(screen.queryByRole("columnheader", { name })).toBeNull();
    for (const name of ["Run details", "Persona", "CSAT", "Turns", "Latency"])
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Columns/ }));
    for (const name of [
      "Scenario",
      "Situation",
      "Sub-goals",
      "Stop latency",
      "AI interruptions",
      "Sub-goal Results",
    ])
      expect(screen.getByRole("menuitem", { name })).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Scenario" }));
    await user.keyboard("{Escape}");
    expect(
      screen.getByRole("columnheader", { name: "Scenario" }),
    ).toBeInTheDocument();
  });

  it("status chips follow Vel, Queued and In progress only while live, zero counts hidden", async () => {
    const user = userEvent.setup();
    const { unmount } = renderRunTable();
    const chips = () =>
      screen
        .getAllByRole("button")
        .filter((b) => b.hasAttribute("aria-pressed"))
        .map((b) => b.getAttribute("aria-label"));

    // Inconclusive is at zero, Flaky and Test invalid have no data: hidden.
    expect(chips()).toEqual(["All", "Failing", "Error", "Passing"]);
    await user.click(screen.getByRole("button", { name: "Failing" }));
    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ filters: { status: ["failed"] } }),
    );
    unmount();

    // A live run adds its queued and running calls.
    useRunCalls.mockImplementation((_id, opts) => ({
      ...calls(opts),
      runActive: true,
      statusCounts: { ...STATUS_COUNTS, queued: 4, in_progress: 2 },
    }));
    renderRunTable();
    expect(chips()).toEqual([
      "All",
      "Queued",
      "In progress",
      "Failing",
      "Error",
      "Passing",
    ]);
  });

  it("status chips keep Failing and Passing at zero", () => {
    useRunCalls.mockImplementation((_id, opts) => ({
      ...calls(opts),
      statusCounts: { inconclusive: 2 },
    }));
    renderRunTable();
    expect(screen.getByRole("button", { name: "Failing" })).toHaveTextContent(
      "0",
    );
    expect(screen.getByRole("button", { name: "Passing" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Inconclusive" }),
    ).toBeInTheDocument();
  });

  it("Group by lists Persona and sends group_by=persona; the other Vel axes carry soon chips", async () => {
    const user = userEvent.setup();
    renderRunTable();

    await user.click(screen.getByRole("button", { name: /Group by/ }));
    const persona = screen.getByRole("menuitem", { name: "Persona" });
    expect(within(persona).queryByText(/soon$/)).toBeNull();
    for (const name of [
      "Rule broken",
      "Failure sub-goal",
      "Failure pattern",
      "Verdict cause",
    ]) {
      const item = screen.getByRole("menuitem", { name: new RegExp(name) });
      expect(within(item).getByText(`${name} soon`)).toBeInTheDocument();
    }
    // Never disabled: a soon axis is still sent.
    await user.click(screen.getByRole("menuitem", { name: /Rule broken/ }));
    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ groupBy: "rule" }),
    );

    await user.click(screen.getByRole("button", { name: /Group by/ }));
    await user.click(screen.getByRole("menuitem", { name: "Persona" }));
    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ groupBy: "persona" }),
    );
  });

  it("falls back to Use case with a note when the server rejects a grouping", async () => {
    useRunCalls.mockImplementation((_id, opts) =>
      opts.groupBy === "persona"
        ? { ...calls(opts), tasks: [], groups: [], error: new Error("400") }
        : calls(opts),
    );
    const user = userEvent.setup();
    renderRunTable();

    await user.click(screen.getByRole("button", { name: /Group by/ }));
    await user.click(screen.getByRole("menuitem", { name: "Persona" }));

    await waitFor(() =>
      expect(useRunCalls).toHaveBeenLastCalledWith(
        "ex1",
        expect.objectContaining({ groupBy: "goal" }),
      ),
    );
    expect(
      screen.getByText(
        "Grouping by Persona isn't available yet. Showing Use case.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load calls/)).toBeNull();
    expect(screen.getByText("Refunds")).toBeInTheDocument();
  });

  it("Run evals on selected and Delete column are flagged", async () => {
    const user = userEvent.setup();
    const onNewRun = vi.fn();
    const { unmount } = render(
      <RerunSelectionBar
        selectedCalls={2}
        scenarioCount={2}
        resolveSelection={async () => ({
          keys: ["a", "b"],
          callIds: ["c1", "c2"],
        })}
        onRerun={onNewRun}
        onClear={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Re-run 2/ }));
    const runEvals = await screen.findByRole("menuitem", {
      name: /Run evals on these calls/,
    });
    expect(within(runEvals).getByLabelText("UI only")).toBeInTheDocument();
    await user.click(runEvals);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only. Nothing was started.",
      { variant: "info" },
    );
    expect(onNewRun).not.toHaveBeenCalled();
    unmount();

    enqueueSnackbar.mockReset();
    const onRerun = vi.fn();
    const onEdit = vi.fn();
    render(
      <EvalColumnActions
        runTestId="rt1"
        canRun
        onRerun={onRerun}
        onEdit={onEdit}
        menuFor={{
          evalId: "eval-1",
          name: "Tone",
          anchorEl: document.body,
        }}
        onClose={vi.fn()}
      />,
    );
    const del = screen.getByRole("menuitem", { name: /Delete column/ });
    expect(within(del).getByLabelText("UI only")).toBeInTheDocument();
    await user.click(del);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only. Nothing was saved.",
      { variant: "info" },
    );
    expect(onRerun).not.toHaveBeenCalled();
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("remove the hardcoded critical: false and show a Must-pass soon chip in the row menu", async () => {
    expect(mapCallRow({ id: "x" })).not.toHaveProperty("critical");

    const onOpen = vi.fn();
    const user = userEvent.setup();
    renderTable({ onOpen });
    await user.click(
      within(callRow("c1")).getByRole("button", { name: "Row actions" }),
    );
    // The menu button works the menu, not the row.
    expect(onOpen).not.toHaveBeenCalled();
    expect(screen.getByText("Must-pass soon")).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Open call" }));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "c1" }));
  });
});
