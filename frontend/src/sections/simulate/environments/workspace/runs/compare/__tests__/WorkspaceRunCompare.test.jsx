import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  renderWithRouter,
  screen,
  waitFor,
  within,
  fireEvent,
} from "src/utils/test-utils";

vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useEnvironmentRuns: vi.fn(),
}));
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallDetail: (id) => ({
    callDetail: { turns: [{ role: "agent", text: `Hello from ${id}` }] },
    isLoading: false,
  }),
}));
// The panel is the run detail's; here it only has to get the right fields.
const filterPanel = vi.fn();
vi.mock("src/components/filter-panel", () => ({
  FilterPanel: (props) => {
    filterPanel(props);
    return null;
  },
}));

// The chart's bars, as the score chart hands them over.
const bars = vi.fn();
vi.mock("../../detail/analytics/DashboardCharts", async (importOriginal) => ({
  ...(await importOriginal()),
  Bars: (props) => {
    bars(props);
    return null;
  },
}));

// Counts the table's renders through a cell only the table draws.
const statusCellRenders = vi.fn();
vi.mock("../compareCells", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    CompareStatusCell: (props) => {
      statusCellRenders();
      return actual.CompareStatusCell(props);
    },
  };
});

const { default: axios } = await import("src/utils/axios");
const { useEnvironmentRuns } = await import(
  "src/api/simulate-environments/runs"
);
const { compareRunsResponse, RUN, CALL } = await import(
  "src/api/simulate-environments/_fixtures/compareRuns"
);
const { RunCompare } = await import("../WorkspaceRunCompare");
const { AGENT_TYPES } = await import("src/sections/agents/constants");
const { TRACE_COLUMNS } = await import(
  "../../detail/trace/traceTable.constants"
);
const { SHARED_COLUMNS } = await import("../compare.constants");
const { histogramRows } = await import("../CompareScoreChart");

const env = { id: "env-1", name: "Refund Support" };
const ROUTE = `/dashboard/simulate/environments/env-1/compare?runs=${RUN.a},${RUN.b},${RUN.c}`;

let lastClient;
const lastParams = () => axios.get.mock.calls.at(-1)[1].params;

function renderCompare({
  response = compareRunsResponse(),
  route = ROUTE,
} = {}) {
  axios.get.mockResolvedValue({ data: response });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  lastClient = client;
  return renderWithRouter(
    <QueryClientProvider client={client}>
      <RunCompare env={env} envState={{}} />
    </QueryClientProvider>,
    { route },
  );
}

const loaded = () => screen.findAllByTestId("compare-run-pill");

beforeEach(() => {
  axios.get.mockReset();
  filterPanel.mockReset();
  bars.mockReset();
  statusCellRenders.mockReset();
  useEnvironmentRuns.mockReturnValue({
    runs: [
      {
        id: "exec-d",
        executionId: "exec-d",
        ordinal: 4,
        label: "Run 4",
        runState: "finished",
        passRate: 80,
      },
      {
        id: "exec-e",
        executionId: "exec-e",
        ordinal: 5,
        label: "Run 5",
        runState: "running",
      },
      {
        id: RUN.a,
        executionId: RUN.a,
        ordinal: 1,
        label: "Run 1",
        runState: "finished",
      },
    ],
    count: 3,
    isLoading: false,
  });
});

describe("RunCompare", () => {
  it("asks for the URL's runs in order, grouped by use case", async () => {
    renderCompare();
    await loaded();
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/compare-runs/",
      expect.anything(),
    );
    expect(lastParams()).toMatchObject({
      runs: `${RUN.a},${RUN.b},${RUN.c}`,
      page: 1,
      group_by: "goal",
      filters: "{}",
    });
  });

  it("shows one pill per run with its whole-run pass rate, the first as baseline", async () => {
    renderCompare();
    const pills = await loaded();
    expect(pills).toHaveLength(3);
    // The label reads agent, muted environment, then pass rate and counts.
    expect(pills[0]).toHaveTextContent(
      "Run 1 · agent v2 × environment v3 · 33% 1/3",
    );
    expect(within(pills[0]).getByText("× environment v3")).toBeInTheDocument();
    expect(within(pills[0]).getByText("baseline")).toBeInTheDocument();
    expect(pills[1]).toHaveTextContent(/ · 100% (\d+)\/\1$/);
    expect(within(pills[1]).queryByText("baseline")).toBeNull();
    expect(
      screen.getByText("3 runs · 2 scenarios · 2 ended differently"),
    ).toBeInTheDocument();
  });

  it("removes a run while more than two remain", async () => {
    renderCompare();
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Remove run 2" }));
    await waitFor(() => expect(lastParams().runs).toBe(`${RUN.a},${RUN.c}`));
  });

  it("offers no remove with two runs", async () => {
    const response = compareRunsResponse();
    response.runs = response.runs.slice(0, 2);
    renderCompare({
      response,
      route: `${ROUTE.split("?")[0]}?runs=${RUN.a},${RUN.b}`,
    });
    await loaded();
    expect(screen.queryByRole("button", { name: /Remove run/ })).toBeNull();
  });

  it("adds a finished run not yet compared", async () => {
    renderCompare();
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Add run" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).queryByText(/Run 5/)).toBeNull();
    expect(within(menu).queryByText(/Run 1/)).toBeNull();
    fireEvent.click(within(menu).getByText(/Run 4/));
    await waitFor(() =>
      expect(lastParams().runs).toBe(`${RUN.a},${RUN.b},${RUN.c},exec-d`),
    );
  });

  it("offers no second remove while the comparison left with two runs loads", async () => {
    renderCompare();
    await loaded();
    axios.get.mockReturnValue(new Promise(() => {}));
    fireEvent.click(screen.getByRole("button", { name: "Remove run 2" }));
    await waitFor(() => expect(lastParams().runs).toBe(`${RUN.a},${RUN.c}`));

    // The previous comparison stays on screen, its header and body alike.
    expect(screen.getAllByTestId("compare-run-pill")).toHaveLength(3);
    expect(
      screen.getByText("3 runs · 2 scenarios · 2 ended differently"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove run/ })).toBeNull();
  });

  it("offers no Add run once eight runs are compared", async () => {
    const ids = Array.from({ length: 8 }, (_, i) => `run-${i}`);
    renderCompare({ route: `${ROUTE.split("?")[0]}?runs=${ids.join(",")}` });
    await loaded();
    expect(screen.getByRole("button", { name: "Add run" })).toBeDisabled();
  });

  it("asks for the URL's runs once each, at most eight", async () => {
    const ids = Array.from({ length: 9 }, (_, i) => `run-${i}`);
    renderCompare({
      route: `${ROUTE.split("?")[0]}?runs=${ids[0]},${ids.join(",")}`,
    });
    await loaded();
    expect(lastParams().runs).toBe(ids.slice(0, 8).join(","));
  });

  it("keeps a loaded comparison on screen when a refetch fails", async () => {
    renderCompare();
    await loaded();
    axios.get.mockRejectedValue({ statusCode: 403, message: "Forbidden" });
    await lastClient.invalidateQueries();

    await waitFor(() =>
      expect(lastClient.getQueryCache().getAll()[0].state.status).toBe("error"),
    );
    expect(screen.queryByText("Couldn't compare these runs")).toBeNull();
    expect(screen.getAllByTestId("compare-run-pill")).toHaveLength(3);
    expect(screen.getByText("refund-double-charge")).toBeInTheDocument();
  });

  it("feeds the Filter panel the run detail's fields from the facets", async () => {
    renderCompare();
    await loaded();
    const { filterFields } = filterPanel.mock.calls.at(-1)[0];
    // The Scenarios tab's fields; Task has no values here, so it is left out.
    expect(filterFields.map((f) => f.value)).toEqual([
      "scenario",
      "goal",
      "situation",
      "persona",
      "age",
      "attack",
      "subGoal",
      "status",
    ]);
    const byValue = Object.fromEntries(filterFields.map((f) => [f.value, f]));
    expect(byValue.goal.choices).toEqual([
      "Refund a double charge",
      "Escalate to a human",
    ]);
    expect(byValue.subGoal.choices).toEqual([
      "identity_verified",
      "refund_created",
    ]);
    expect(byValue.persona).toMatchObject({
      label: "Name",
      choices: ["Elena Vargas", "Arjun Mehta"],
    });
    // Coverage and sub-goal values read as the Scenarios tab names them.
    expect(byValue.subGoal.choiceLabels).toMatchObject({
      identity_verified: "Identity verified",
    });
    expect(byValue.attack.choiceLabels).toBe(byValue.subGoal.choiceLabels);
    expect(byValue.persona.choiceLabels).toBeUndefined();
  });

  it("sends an applied filter as the server's filter", async () => {
    renderCompare();
    await loaded();
    filterPanel.mock.calls
      .at(-1)[0]
      .onApply([{ field: "subGoal", value: ["refund_created"] }]);
    await waitFor(() =>
      expect(lastParams().filters).toBe('{"sub_goal":["refund_created"]}'),
    );
  });

  it("goes back to the first page on a new grouping", async () => {
    const response = compareRunsResponse();
    response.total_pages = 3;
    renderCompare({ response });
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Go to page 2" }));
    await waitFor(() => expect(lastParams().page).toBe(2));

    fireEvent.click(screen.getByRole("button", { name: /Group by/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Status" }));
    await waitFor(() =>
      expect(lastParams()).toMatchObject({ page: 1, group_by: "status" }),
    );
  });

  const toPage2 = async () => {
    fireEvent.click(screen.getByRole("button", { name: "Go to page 2" }));
    await waitFor(() => expect(lastParams().page).toBe(2));
  };
  const pagedResponse = () => {
    const response = compareRunsResponse();
    response.total_pages = 3;
    return response;
  };

  it("goes back to the first page on an applied filter", async () => {
    renderCompare({ response: pagedResponse() });
    await loaded();
    await toPage2();

    filterPanel.mock.calls
      .at(-1)[0]
      .onApply([{ field: "subGoal", value: ["refund_created"] }]);

    await waitFor(() =>
      expect(lastParams()).toMatchObject({
        filters: '{"sub_goal":["refund_created"]}',
        page: 1,
      }),
    );
  });

  it("goes back to the first page on a status chip", async () => {
    renderCompare({ response: pagedResponse() });
    await loaded();
    await toPage2();

    fireEvent.click(screen.getByRole("button", { name: "Failed" }));

    await waitFor(() =>
      expect(lastParams()).toMatchObject({
        filters: '{"status":["failed"]}',
        page: 1,
      }),
    );
  });

  it("leaves the table alone when the runs list polls", async () => {
    const { rerender } = renderCompare();
    await loaded();
    await waitFor(() => expect(statusCellRenders).toHaveBeenCalled());
    const before = statusCellRenders.mock.calls.length;
    // Each poll hands back new run objects.
    const polled = useEnvironmentRuns().runs.map((run) => ({ ...run }));
    useEnvironmentRuns.mockReturnValue({ runs: polled, count: 3 });

    rerender(
      <QueryClientProvider client={lastClient}>
        <RunCompare env={env} envState={{}} />
      </QueryClientProvider>,
    );

    expect(screen.getAllByTestId("compare-run-pill")).toHaveLength(3);
    expect(statusCellRenders.mock.calls.length).toBe(before);
  });

  it("shows the run detail's default columns plus Status", async () => {
    renderCompare();
    await loaded();
    const heads = (await screen.findAllByRole("columnheader")).map(
      (th) => th.textContent,
    );
    // Scenario and its own columns lead, then the unlabelled run chip and each
    // run's columns; evals come last.
    const detailDefaults = TRACE_COLUMNS.filter(
      (c) => c.defaultOn && c.key !== "scenario",
    );
    const label = (c) => c.label;
    expect(heads).toEqual([
      "Scenario",
      ...detailDefaults.filter((c) => SHARED_COLUMNS.has(c.key)).map(label),
      "",
      "Status",
      ...detailDefaults.filter((c) => !SHARED_COLUMNS.has(c.key)).map(label),
      "Refund correctness",
      "Tone",
    ]);
    expect(heads.slice(0, 4)).toEqual(["Scenario", "Persona", "", "Status"]);
  });

  it("leads with a scenario column turned on in the picker", async () => {
    const user = userEvent.setup();
    renderCompare();
    await loaded();
    await user.click(screen.getByRole("button", { name: /Columns/ }));
    await user.click(
      screen.getByRole("menuitem", { name: "Conversation branch" }),
    );
    await user.keyboard("{Escape}");
    const heads = (await screen.findAllByRole("columnheader")).map(
      (th) => th.textContent,
    );
    expect(heads.slice(0, 5)).toEqual([
      "Scenario",
      "Persona",
      "Conversation branch",
      "",
      "Status",
    ]);
  });

  it("draws a row's shared cells before its first run chip", async () => {
    renderCompare();
    await loaded();
    const [firstLine] = document.querySelectorAll("tbody tr[data-run]");
    const cells = [...firstLine.children];
    const persona = cells.findIndex((td) =>
      td.textContent.includes("Impatient caller"),
    );
    const chip = cells.findIndex((td) =>
      td.querySelector('[role="img"][aria-label^="Run "]'),
    );
    expect(persona).toBe(1);
    expect(chip).toBeGreaterThan(persona);
  });

  it("draws a scenario's own columns once across its run lines", async () => {
    renderCompare();
    await loaded();
    // The refund row: two trials in runs 1 and 2, one Not run line for run 3.
    expect(screen.getAllByText("Impatient caller")).toHaveLength(1);
    expect(screen.getAllByText("refund-double-charge · Trial 2")).toHaveLength(
      2,
    );
    expect(screen.getAllByText("Not run")).toHaveLength(1);
    // The row is named as its calls are on the run detail.
    expect(screen.getByText("refund-double-charge")).toBeInTheDocument();
  });

  it("lists a call's sub-goals on hover of its count", async () => {
    renderCompare();
    await loaded();
    fireEvent.mouseOver(screen.getAllByText("1/2 sub-goals")[0]);
    const tip = await screen.findByRole("tooltip");
    expect(within(tip).getByText("refund_created")).toBeInTheDocument();
    // Each verdict is named, as on the run detail.
    expect(
      within(tip).getByRole("img", { name: "Passed" }),
    ).toBeInTheDocument();
    expect(
      within(tip).getByRole("img", { name: "Failed" }),
    ).toBeInTheDocument();
  });

  it("shows group headers with each run's pass rate", async () => {
    renderCompare();
    await loaded();
    const head = document.querySelector('tr[aria-expanded="true"]');
    expect(
      within(head).getByText("Refund a double charge"),
    ).toBeInTheDocument();
    expect(within(head).getByText("50%")).toBeInTheDocument();
    expect(within(head).getAllByText("100%").length).toBeGreaterThan(0);
  });

  it("shows a group's eval as grading while a run's call waits on it", async () => {
    const response = compareRunsResponse();
    response.rows[0].calls[RUN.a][0].evaluations[0].status = "pending";
    renderCompare({ response });
    await loaded();
    const [refund, escalate] = document.querySelectorAll("tr[aria-expanded]");
    // Refund correctness has no group score; only run 1's refund call grades.
    expect(refund.querySelectorAll(".MuiSkeleton-root")).toHaveLength(1);
    expect(escalate.querySelectorAll(".MuiSkeleton-root")).toHaveLength(0);
    // A scored group eval reads as the run detail's: mean over scored calls.
    expect(within(refund).getAllByText(/^Avg · \d+ scored$/).length).toBe(2);
  });

  it("hides the voice-only columns for chat calls of runs with no agent type", async () => {
    const response = compareRunsResponse();
    response.runs.forEach((run) => {
      run.execution.agent_type = null;
    });
    response.rows.forEach((row) =>
      Object.values(row.calls).forEach((calls) =>
        calls.forEach((call) => {
          call.modality = AGENT_TYPES.CHAT;
        }),
      ),
    );
    renderCompare({ response });
    await loaded();
    const heads = screen
      .getAllByRole("columnheader")
      .map((th) => th.textContent);
    expect(heads).toContain("Latency");
    expect(heads).not.toContain("Stop latency");
    expect(heads).not.toContain("AI interruptions");
  });

  it("draws arrows against the baseline only with Show diff on", async () => {
    renderCompare();
    await loaded();
    expect(screen.queryAllByTestId("diff-arrow")).toHaveLength(0);
    expect(screen.queryByText("was Failed")).toBeNull();

    fireEvent.click(screen.getByRole("checkbox", { name: "Show diff" }));

    // Run 2's trial 1 is faster than the baseline's trial 1: better, so down,
    // said in words for a screen reader.
    expect(screen.getByText("down 50ms, better")).toBeInTheDocument();
    // Run 2's trial 2 passed where the baseline's trial 2 failed.
    expect(screen.getAllByText("was Failed").length).toBeGreaterThan(0);
    // The baseline's own lines carry none.
    const baselineLines = document.querySelectorAll(`tr[data-run="${RUN.a}"]`);
    baselineLines.forEach((tr) =>
      expect(within(tr).queryAllByTestId("diff-arrow")).toHaveLength(0),
    );
  });

  it("opens a row's calls side by side, one run per column", async () => {
    renderCompare();
    await loaded();
    fireEvent.click(screen.getAllByText("refund-double-charge · Trial 1")[0]);
    expect(
      await screen.findByText(`Hello from ${CALL.aRefund1}`),
    ).toBeInTheDocument();
    expect(screen.getByText(`Hello from ${CALL.bRefund1}`)).toBeInTheDocument();
    // Only the picked trial's transcripts load; run 3 did not play the row.
    expect(screen.queryByText(`Hello from ${CALL.aRefund2}`)).toBeNull();
    expect(screen.getAllByRole("button", { name: "Open call" })).toHaveLength(
      2,
    );

    fireEvent.click(screen.getByRole("button", { name: "Trial 2" }));

    expect(screen.getByText(`Hello from ${CALL.aRefund2}`)).toBeInTheDocument();
    expect(screen.getByText(`Hello from ${CALL.bRefund2}`)).toBeInTheDocument();
    expect(screen.queryByText(`Hello from ${CALL.aRefund1}`)).toBeNull();
  });

  it("counts a group's scenarios on every page, not only this one", async () => {
    renderCompare();
    await loaded();
    const head = document.querySelector('tr[aria-expanded="true"]');
    expect(within(head).getByText("3 scenarios")).toBeInTheDocument();
  });

  it("keeps the rows on screen while a new grouping loads", async () => {
    renderCompare();
    await loaded();
    const ungrouped = compareRunsResponse();
    ungrouped.groups = [];
    axios.get.mockResolvedValueOnce({ data: ungrouped });
    fireEvent.click(screen.getByRole("button", { name: /Group by/ }));
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "No grouping" }),
    );
    await waitFor(() => expect(lastParams()).not.toHaveProperty("group_by"));
    await waitFor(() =>
      expect(document.querySelector("tr[aria-expanded]")).toBeNull(),
    );

    axios.get.mockReturnValueOnce(new Promise(() => {}));
    fireEvent.click(screen.getByRole("button", { name: /Group by/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Status" }));

    await waitFor(() => expect(lastParams().group_by).toBe("status"));
    expect(screen.getByText("refund-double-charge")).toBeInTheDocument();
  });

  it("filters by status from the chips, counted over every run", async () => {
    renderCompare();
    await loaded();
    // 1 + 3 + 1 passed across the three runs.
    const passed = screen.getByRole("button", { name: "Passed" });
    expect(within(passed).getByText("5")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Failed" }));

    await waitFor(() =>
      expect(lastParams().filters).toBe('{"status":["failed"]}'),
    );
  });

  it("names a refused run and offers to drop it", async () => {
    axios.get.mockReset();
    axios.get.mockRejectedValue({
      statusCode: 400,
      error: "comparison_run_failed",
      message: "This run failed before its calls were graded",
      execution_id: RUN.a,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    renderWithRouter(
      <QueryClientProvider client={client}>
        <RunCompare env={env} envState={{}} />
      </QueryClientProvider>,
      { route: ROUTE },
    );

    expect(
      await screen.findByText("Run 1 can't be compared"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("This run failed before its calls were graded"),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("compare-run-pill")).toHaveLength(3);

    axios.get.mockResolvedValue({ data: compareRunsResponse() });
    fireEvent.click(screen.getByText("Remove run 1"));

    await waitFor(() => expect(lastParams().runs).toBe(`${RUN.b},${RUN.c}`));
  });

  const plotted = () => bars.mock.calls.at(-1)[0].rows;

  it("charts the first evaluation that has scores", async () => {
    const response = compareRunsResponse();
    // The server sends every catalog eval's bands, empty when none scored.
    response.runs.forEach((run) => {
      run.histograms["eval-1"] = Array(10).fill(0);
    });
    renderCompare({ response });
    await loaded();

    // Refund correctness has no scores, so Tone is charted.
    expect(plotted()[5]).toMatchObject({ [RUN.a]: 1, [RUN.b]: 0, [RUN.c]: 0 });
    expect(plotted()[9]).toMatchObject({ [RUN.a]: 1, [RUN.b]: 2, [RUN.c]: 1 });
  });

  it("charts sub-goal scores after evaluations", async () => {
    const response = compareRunsResponse();
    response.evaluation_columns = [
      ...response.evaluation_columns,
      { id: "goal-1", name: "Identity verified", kind: "sub_goal" },
    ];
    response.runs.forEach((run) => {
      run.histograms = {
        "eval-1": Array(10).fill(0),
        "eval-2": Array(10).fill(0),
        "goal-1": [0, 0, 0, 0, 0, 0, 0, 0, 0, 2],
      };
    });
    renderCompare({ response });
    await loaded();

    expect(screen.queryByText("No evaluation scores in these runs")).toBeNull();
    expect(plotted()[9]).toMatchObject({ [RUN.a]: 2, [RUN.b]: 2, [RUN.c]: 2 });
    expect(
      screen.getByRole("combobox", { name: "Evaluation" }),
    ).toHaveTextContent("Identity verified");
  });

  it("charts the evaluation picked", async () => {
    renderCompare();
    await loaded();
    expect(plotted()[0]).toMatchObject({ [RUN.a]: 1 });

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Evaluation" }));
    fireEvent.click(await screen.findByRole("option", { name: "Tone" }));

    await waitFor(() => expect(plotted()[0][RUN.a]).toBe(0));
    expect(plotted()[8]).toMatchObject({ [RUN.a]: 0, [RUN.b]: 1, [RUN.c]: 0 });
  });

  it("colours each compared run by its place, so no two share a colour", async () => {
    const response = compareRunsResponse();
    // Runs 1 and 9 wrap to the same colour by number.
    response.runs[1].execution.ordinal = 9;
    renderCompare({ response });
    await loaded();

    const { series } = bars.mock.calls.at(-1)[0];
    expect(series.map((one) => one.colorIndex)).toEqual([0, 1, 2]);
    expect(series[1].label).toMatch(/^9 · /);
  });

  it("moves to the last page when a refetch leaves fewer pages", async () => {
    const paged = compareRunsResponse();
    paged.total_pages = 3;
    renderCompare({ response: paged });
    await loaded();
    const shrunk = compareRunsResponse();
    Object.assign(shrunk, { rows: [], groups: [], page: 3, total_pages: 2 });
    axios.get.mockResolvedValue({ data: shrunk });

    fireEvent.click(screen.getByRole("button", { name: "Go to page 3" }));

    await waitFor(() => expect(lastParams().page).toBe(3));
    await waitFor(() => expect(lastParams().page).toBe(2));
  });

  it("offers only numbered trials when a run's calls have none", async () => {
    const response = compareRunsResponse();
    const [first] = response.rows[0].calls[RUN.a];
    response.rows[0].calls[RUN.a] = [{ ...first, trial_index: null }];
    renderCompare({ response });
    await loaded();
    fireEvent.click(screen.getAllByText("refund-double-charge · Trial 1")[0]);

    expect(
      await screen.findByRole("button", { name: "Trial 1" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Trial 2" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Trial null" })).toBeNull();
    // The untagged baseline call stands in for every trial.
    expect(screen.getByText(`Hello from ${CALL.aRefund1}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Trial 2" }));
    expect(screen.getByText(`Hello from ${CALL.aRefund1}`)).toBeInTheDocument();
    expect(screen.getByText(`Hello from ${CALL.bRefund2}`)).toBeInTheDocument();
  });

  it("asks for two runs when the URL has fewer", () => {
    renderCompare({ route: `${ROUTE.split("?")[0]}?runs=${RUN.a}` });
    expect(
      screen.getByText("Pick at least 2 finished runs to compare"),
    ).toBeInTheDocument();
    expect(axios.get).not.toHaveBeenCalled();
  });
});

describe("histogramRows", () => {
  it("is ten bands, each with every run's count under its id", () => {
    const rows = histogramRows(
      [
        { id: "a", histograms: { tone: [1, 0, 0, 0, 0, 2, 0, 0, 0, 3] } },
        { id: "b", histograms: {} },
      ],
      "tone",
    );

    expect(rows).toHaveLength(10);
    expect(rows[0]).toEqual({ label: "0–10%", a: 1, b: 0 });
    expect(rows[5]).toEqual({ label: "50–60%", a: 2, b: 0 });
    expect(rows[9]).toEqual({ label: "90–100%", a: 3, b: 0 });
  });
});
