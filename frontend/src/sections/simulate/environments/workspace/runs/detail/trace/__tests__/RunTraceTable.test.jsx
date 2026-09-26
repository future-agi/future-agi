import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The wrapper owns the network hook; feed it a fixed set of mapped tasks.
const useRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useRunCalls: (...args) => useRunCalls(...args),
}));

const { default: RunTraceTable } = await import("../RunTraceTable");
const { default: TraceGroupHeaderRow } = await import("../TraceGroupHeaderRow");

const TASKS = [
  {
    id: "t1",
    scenario: "Refund a double charge",
    goal: "Refund a double charge",
    subGoals: ["Identity verified", "Refund created"],
    persona: "Impatient caller",
    personaDetails: {
      name: "The Hungry Customer in a Rush",
      voice: "US male",
      age: "34",
      traits: ["impatient", "in a hurry"],
    },
    status: "passed",
    critical: false,
    csat: 8,
    turns: 5,
    latencyMs: 300,
    tokens: null,
    durationMs: 40000,
    evalResults: [{ id: "eval-1", name: "Tone", score: 0.9, passed: true }],
  },
  {
    id: "t2",
    scenario: "Escalate to a human",
    goal: "Escalate to a human",
    subGoals: ["Transferred to human"],
    persona: "Angry caller",
    personaDetails: {
      name: "Angry caller",
      voice: null,
      age: null,
      traits: [],
    },
    status: "failed",
    critical: false,
    csat: 3,
    turns: 12,
    latencyMs: 600,
    tokens: null,
    durationMs: 80000,
    evalResults: [{ id: "eval-1", name: "Tone", score: 0.3, passed: false }],
  },
  {
    id: "t3",
    scenario: "Handle a timeout",
    goal: "Handle a timeout",
    subGoals: [],
    persona: "Caller",
    personaDetails: {
      name: "Caller",
      voice: null,
      age: null,
      traits: [],
    },
    status: "error",
    critical: false,
    csat: null,
    turns: null,
    latencyMs: null,
    tokens: null,
    durationMs: null,
    evalResults: [],
  },
];
const COLUMNS = [{ key: "eval-1", label: "Tone", group: "Evaluations" }];

const groupsFor = (tasks, groupBy = "goal") => {
  const grouped = new Map();
  tasks.forEach((task) => {
    const key = groupBy === "status" ? task.status : task.goal;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(task);
  });
  const labels = {
    passed: "Passed",
    failed: "Failed",
    error: "Errored",
  };
  return [...grouped].map(([key, rows]) => ({
    label: labels[key] || key,
    rows,
    count: rows.length,
    measured: rows.length,
    passed: rows.filter((row) => row.status === "passed").length,
    agg: { csat: null, turns: null, latency: null, tokens: null, evals: {} },
  }));
};

const FACETS = {
  goal: TASKS.map((task) => ({ value: task.goal, count: 1 })),
  sub_goal: [
    { value: "Identity verified", count: 1 },
    { value: "Refund created", count: 1 },
    { value: "Transferred to human", count: 1 },
  ],
  status: [
    { value: "passed", count: 1 },
    { value: "failed", count: 1 },
    { value: "error", count: 1 },
  ],
};

const renderTable = (props = {}) =>
  render(<RunTraceTable executionId="ex1" onOpenCall={vi.fn()} {...props} />);

describe("RunTraceTable", () => {
  it("renders full-group aggregates independently of the visible page", () => {
    render(
      <table>
        <tbody>
          <TraceGroupHeaderRow
            group={{
              label: "Refunds",
              rows: [TASKS[0]],
              count: 8,
              measured: 8,
              passed: 6,
              agg: {
                csat: 7.5,
                turns: 4.5,
                latency: 250,
                tokens: 1200,
                evals: { "eval-1": { scored: 8, scoreSum: 6 } },
              },
            }}
            collapsed={false}
            onToggle={vi.fn()}
            show={() => true}
            showEvals
            evals={[{ id: "eval-1" }]}
            selected={new Set()}
          />
        </tbody>
      </table>,
    );
    for (const value of [
      "7.5",
      "4.5",
      "250ms",
      "1,200",
      "75%",
      "Avg · 8 scored",
      "Total",
    ]) {
      expect(screen.getByText(value)).toBeInTheDocument();
    }
  });

  beforeEach(() => {
    useRunCalls.mockImplementation((_executionId, opts = {}) => {
      const status = opts.filters?.status?.[0];
      const tasks = status
        ? TASKS.filter((task) => task.status === status)
        : TASKS;
      return {
        tasks,
        columns: COLUMNS,
        groups: groupsFor(tasks, opts.groupBy),
        facets: FACETS,
        count: tasks.length,
        totalPages: 1,
        isLoading: false,
      };
    });
  });

  it("renders the real calls, grouped by scenario, with the eval column", async () => {
    const user = userEvent.setup();
    renderTable();

    // Groups (one per scenario) render; the eval column header is present.
    expect(screen.getByText("Refund a double charge")).toBeInTheDocument();
    expect(screen.getByText("Tone")).toBeInTheDocument();

    // Groups start collapsed — expand to reveal the rows, then the persona cell.
    await user.click(screen.getByRole("button", { name: /Expand all/ }));
    expect(
      screen.getByText("The Hungry Customer in a Rush"),
    ).toBeInTheDocument();
    expect(screen.getByText("US male")).toBeInTheDocument();
    expect(screen.getByText("34")).toBeInTheDocument();
    expect(screen.getByText("impatient, in a hurry")).toBeInTheDocument();
  });

  it("fires onOpenCall with the task on a row click", async () => {
    const user = userEvent.setup();
    const onOpenCall = vi.fn();
    renderTable({ onOpenCall });

    await user.click(screen.getByRole("button", { name: /Expand all/ }));
    await user.click(screen.getByText("The Hungry Customer in a Rush"));

    expect(onOpenCall).toHaveBeenCalledWith(
      expect.objectContaining({ id: "t1" }),
    );
  });

  it("narrows the rows when a status chip is clicked", async () => {
    const user = userEvent.setup();
    renderTable();

    // All three scenarios show at first.
    expect(screen.getByText("Refund a double charge")).toBeInTheDocument();
    // The chip is translated into an API filter; the server result replaces
    // the rendered rows.
    await user.click(screen.getByRole("button", { name: "Failing" }));

    expect(screen.queryByText("Refund a double charge")).toBeNull();
    expect(screen.getByText("Escalate to a human")).toBeInTheDocument();
    expect(screen.queryByText("Handle a timeout")).toBeNull();
    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ filters: { status: ["failed"] } }),
    );

    await user.click(screen.getByRole("button", { name: "Errored" }));
    expect(screen.queryByText("Escalate to a human")).toBeNull();
    expect(screen.getByText("Handle a timeout")).toBeInTheDocument();
    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ filters: { status: ["error"] } }),
    );
  });

  it("scopes to handed-over calls until the affected-calls chip is dismissed", async () => {
    const user = userEvent.setup();
    const { container } = renderTable({
      initialFilters: { callExecutionId: ["t1", "t2"] },
    });

    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ filters: { call_execution_id: ["t1", "t2"] } }),
    );
    expect(screen.getByText("2 affected calls")).toBeInTheDocument();
    // The Filter button counts only the panel's own filters.
    expect(screen.getByRole("button", { name: "Filter" })).toBeInTheDocument();

    await user.click(container.querySelector(".MuiChip-deleteIcon"));

    expect(screen.queryByText("2 affected calls")).toBeNull();
    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ filters: {} }),
    );
  });

  it("keeps the affected-calls scope while a status chip narrows within it", async () => {
    const user = userEvent.setup();
    renderTable({ initialFilters: { callExecutionId: ["t1", "t2"] } });

    await user.click(screen.getByRole("button", { name: "Failing" }));

    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({
        filters: { call_execution_id: ["t1", "t2"], status: ["failed"] },
      }),
    );
  });

  it("exposes later server pages for runs with more than 100 trials", async () => {
    const user = userEvent.setup();
    const finalTrial = {
      ...TASKS[0],
      id: "trial-200",
      goal: "Scenario 200",
      scenario: "Scenario 200 · Trial 20",
    };
    useRunCalls.mockImplementation((_executionId, opts = {}) => {
      const tasks = opts.page === 2 ? [finalTrial] : TASKS;
      return {
        tasks,
        columns: COLUMNS,
        groups: groupsFor(tasks, opts.groupBy),
        facets: FACETS,
        count: 200,
        totalPages: 2,
        isLoading: false,
      };
    });
    renderTable();

    await user.click(screen.getByRole("button", { name: "Go to page 2" }));

    expect(screen.getByText("Scenario 200")).toBeInTheDocument();
    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ page: 2, limit: 50 }),
    );
  });

  it("applies column picker choices to the rendered table", async () => {
    const user = userEvent.setup();
    renderTable();

    expect(
      screen.getByRole("columnheader", { name: "Latency" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Columns/ }));
    await user.click(screen.getByRole("menuitem", { name: "Latency" }));

    expect(screen.queryByRole("columnheader", { name: "Latency" })).toBeNull();
  });

  it("re-buckets the rows when the group-by axis changes to Status", async () => {
    const user = userEvent.setup();
    renderTable();

    await user.click(screen.getByRole("button", { name: /Group by/ }));
    await user.click(screen.getByRole("menuitem", { name: "Status" }));

    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ groupBy: "status" }),
    );

    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getAllByText("Errored")).not.toHaveLength(0);
  });

  it("offers the Scenarios tab's axes plus Status and requests the chosen one", async () => {
    const user = userEvent.setup();
    renderTable();

    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ groupBy: "goal" }),
    );
    await user.click(screen.getByRole("button", { name: /Group by/ }));
    expect(
      screen.getAllByRole("menuitem").map((item) => item.textContent),
    ).toEqual([
      "Use case",
      "Sub-goal",
      "Accent",
      "Age",
      "Attack",
      "Task",
      "Status",
    ]);
    await user.click(screen.getByRole("menuitem", { name: "Task" }));

    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ groupBy: "task" }),
    );
  });

  it("offers only Goal, Sub goal, and Status filters", async () => {
    const user = userEvent.setup();
    renderTable();

    await user.click(screen.getByRole("button", { name: /Filter/ }));

    const [fieldPicker] = screen.getAllByRole("combobox");
    await user.click(fieldPicker);
    expect(screen.getByRole("option", { name: "Goal" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Sub goal" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Status" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Persona" })).toBeNull();
    expect(screen.queryByRole("option", { name: "Scenario" })).toBeNull();
  });

  it("shows an empty state instead of a table when there are no calls", () => {
    useRunCalls.mockReturnValue({
      tasks: [],
      columns: [],
      groups: [],
      facets: {},
      count: 0,
      totalPages: 1,
      isLoading: false,
    });
    renderTable();
    expect(screen.getByText(/No calls match that filter/)).toBeInTheDocument();
  });

  it("shows an error state, not the empty filter state, when the calls request fails", () => {
    useRunCalls.mockReturnValue({
      tasks: [],
      columns: [],
      groups: [],
      facets: {},
      count: 0,
      totalPages: 1,
      isLoading: false,
      error: new Error("boom"),
    });
    renderTable();
    expect(screen.getByText(/Couldn't load calls/i)).toBeInTheDocument();
    expect(screen.queryByText(/No calls match that filter/)).toBeNull();
  });
});
