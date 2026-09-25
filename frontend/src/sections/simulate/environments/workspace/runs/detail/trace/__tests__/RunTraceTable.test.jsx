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

  it("reports the exact list query it reads, and clears it on unmount", async () => {
    const user = userEvent.setup();
    const onQueryChange = vi.fn();
    const { unmount } = renderTable({ onQueryChange });

    const base = { page: 1, limit: 50, search: "", filters: {}, groupBy: "goal" };
    expect(onQueryChange).toHaveBeenLastCalledWith(base);
    // What it reports is what it asked the list for — the drawer reads the
    // same cache entry.
    expect(useRunCalls).toHaveBeenLastCalledWith("ex1", base);

    await user.click(screen.getByRole("button", { name: /Group by/ }));
    await user.click(screen.getByRole("menuitem", { name: "Status" }));
    expect(onQueryChange).toHaveBeenLastCalledWith({ ...base, groupBy: "status" });

    await user.click(screen.getByRole("button", { name: "Failing" }));
    expect(onQueryChange).toHaveBeenLastCalledWith({
      ...base,
      groupBy: "status",
      filters: { status: ["failed"] },
    });

    unmount();
    expect(onQueryChange).toHaveBeenLastCalledWith(null);
  });

  it("follows the drawer: switches page, expands the call's group, highlights its row", () => {
    const onQueryChange = vi.fn();
    const { rerender } = renderTable({ onQueryChange });
    // Groups start collapsed, so no call rows are visible.
    expect(screen.queryByText("Escalate to a human · Trial 1")).toBeNull();
    expect(screen.queryAllByRole("row", { selected: true })).toHaveLength(0);

    rerender(
      <RunTraceTable
        executionId="ex1"
        onOpenCall={vi.fn()}
        onQueryChange={onQueryChange}
        activeCallId="t2"
        activePage={2}
      />,
    );

    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ page: 2 }),
    );
    expect(onQueryChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2 }),
    );
    const selected = screen.getAllByRole("row", { selected: true });
    expect(selected).toHaveLength(1);
    expect(selected[0]).toHaveTextContent("Angry caller");
    // Only the open call's group expanded; the others stay collapsed.
    expect(screen.queryByText("Impatient caller")).toBeNull();
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
