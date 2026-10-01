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
const { TRACE_COLUMNS, VOICE_ONLY_COLUMNS } = await import(
  "../traceTable.constants"
);

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

  describe("which groups are open", () => {
    const persona = {
      refund: () => screen.queryByText("The Hungry Customer in a Rush"),
      escalate: () => screen.queryByText("Angry caller"),
      timeout: () => screen.queryByText("Caller"),
    };
    const header = (label) => screen.getAllByText(label)[0];
    const expandAll = () =>
      screen.getByRole("checkbox", { name: /Expand all/ });

    it("leaves the other groups closed after opening the only group a filter showed", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(screen.getByRole("button", { name: "Failing" }));
      await user.click(header("Escalate to a human"));
      await user.click(screen.getByRole("button", { name: /^All/ }));

      expect(persona.escalate()).toBeInTheDocument();
      expect(persona.refund()).toBeNull();
      expect(persona.timeout()).toBeNull();
    });

    it("turns Expand all on only once every group is open", async () => {
      const user = userEvent.setup();
      renderTable();
      expect(expandAll()).not.toBeChecked();
      await user.click(header("Refund a double charge"));
      await user.click(header("Escalate to a human"));
      expect(expandAll()).not.toBeChecked();
      await user.click(header("Handle a timeout"));
      expect(expandAll()).toBeChecked();

      await user.click(expandAll());
      expect(persona.refund()).toBeNull();
      expect(persona.escalate()).toBeNull();
      expect(persona.timeout()).toBeNull();
    });

    it("keeps Expand all on through a Group by change", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(expandAll());
      await user.click(screen.getByRole("button", { name: /Group by/ }));
      await user.click(screen.getByRole("menuitem", { name: "Status" }));

      expect(expandAll()).toBeChecked();
      expect(persona.refund()).toBeInTheDocument();
      expect(persona.escalate()).toBeInTheDocument();
      expect(persona.timeout()).toBeInTheDocument();
    });

    it("keeps the open call's group closed once the user closes it, across a filter's loading", async () => {
      const user = userEvent.setup();
      let loading = false;
      const base = useRunCalls.getMockImplementation();
      useRunCalls.mockImplementation((...args) =>
        loading
          ? { ...base(...args), tasks: [], groups: [], isLoading: true }
          : base(...args),
      );
      const { rerender } = renderTable({ activeCallId: "t2" });
      expect(persona.escalate()).toBeInTheDocument();
      await user.click(header("Escalate to a human"));
      expect(persona.escalate()).toBeNull();

      loading = true;
      await user.click(screen.getByRole("button", { name: "Failing" }));
      expect(screen.getByText("Loading calls…")).toBeInTheDocument();
      loading = false;
      rerender(
        <RunTraceTable
          executionId="ex1"
          onOpenCall={vi.fn()}
          activeCallId="t2"
        />,
      );

      expect(persona.escalate()).toBeNull();
    });
  });

  it("loads a group row while the run is going and the group's calls are on other pages", () => {
    const group = {
      label: "Refunds",
      rows: [{ ...TASKS[0], executionStatus: "completed" }],
      count: 3,
      agg: {},
    };
    useRunCalls.mockReturnValue({
      tasks: group.rows,
      columns: COLUMNS,
      groups: [group],
      facets: FACETS,
      count: 3,
      totalPages: 2,
      isLoading: false,
      runActive: true,
    });
    renderTable();
    const groupRow = screen.getByText("Refunds").closest("tr");
    expect(groupRow.querySelector(".MuiSkeleton-root")).not.toBeNull();
  });

  it("renders the real calls, grouped by scenario, with the eval column", async () => {
    const user = userEvent.setup();
    renderTable();

    // Groups (one per scenario) render; the eval column header is present.
    expect(screen.getByText("Refund a double charge")).toBeInTheDocument();
    expect(screen.getByText("Tone")).toBeInTheDocument();

    // Groups start collapsed — expand to reveal the rows, then the persona cell.
    await user.click(screen.getByRole("checkbox", { name: /Expand all/ }));
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

    await user.click(screen.getByRole("checkbox", { name: /Expand all/ }));
    await user.click(screen.getByText("The Hungry Customer in a Rush"));

    expect(onOpenCall).toHaveBeenCalledWith(
      expect.objectContaining({ id: "t1" }),
    );
  });

  it("reports the exact list query it reads, and clears it on unmount", async () => {
    const user = userEvent.setup();
    const onQueryChange = vi.fn();
    const { unmount } = renderTable({ onQueryChange });

    const base = {
      page: 1,
      limit: 50,
      search: "",
      filters: {},
      groupBy: "goal",
    };
    expect(onQueryChange).toHaveBeenLastCalledWith(base);
    // What it reports is what it asked the list for — the drawer reads the
    // same cache entry.
    expect(useRunCalls).toHaveBeenLastCalledWith("ex1", base);

    await user.click(screen.getByRole("button", { name: /Group by/ }));
    await user.click(screen.getByRole("menuitem", { name: "Status" }));
    expect(onQueryChange).toHaveBeenLastCalledWith({
      ...base,
      groupBy: "status",
    });

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

  // A status chip the list hasn't fetched yet shows "Loading calls…" in the
  // table's place, so the table unmounts. The groups the user opened must
  // survive that. A row's persona name only shows while its group is open.
  it("keeps a group the user expanded open while a filter loads", async () => {
    const user = userEvent.setup();
    let loading = false;
    const base = useRunCalls.getMockImplementation();
    useRunCalls.mockImplementation((...args) =>
      loading
        ? { ...base(...args), tasks: [], groups: [], isLoading: true }
        : base(...args),
    );
    const { rerender } = renderTable();

    await user.click(screen.getByText("Escalate to a human"));
    expect(screen.getByText("Angry caller")).toBeInTheDocument();

    loading = true;
    await user.click(screen.getByRole("button", { name: "Failing" }));
    expect(screen.getByText("Loading calls…")).toBeInTheDocument();

    loading = false;
    rerender(<RunTraceTable executionId="ex1" onOpenCall={vi.fn()} />);
    expect(screen.getByText("Angry caller")).toBeInTheDocument();
  });

  it("keeps each group's own state through a filter with no matches", async () => {
    const user = userEvent.setup();
    // The chip's count says one call is inconclusive, but the list comes back
    // empty (the count is from before a refetch), so the table gives way to
    // the empty state.
    const base = useRunCalls.getMockImplementation();
    useRunCalls.mockImplementation((...args) => ({
      ...base(...args),
      facets: {
        ...FACETS,
        status: [...FACETS.status, { value: "inconclusive", count: 1 }],
      },
    }));
    renderTable();

    await user.click(screen.getByRole("checkbox", { name: /Expand all/ }));
    // Expanded, the label shows in the group header and again in the row; the
    // header comes first.
    await user.click(screen.getAllByText("Refund a double charge")[0]);
    expect(screen.queryByText("The Hungry Customer in a Rush")).toBeNull();

    await user.click(screen.getByRole("button", { name: /Inconclusive/ }));
    expect(screen.getByText("No calls match that filter")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^All/ }));

    expect(screen.queryByText("The Hungry Customer in a Rush")).toBeNull();
    expect(screen.getByText("Angry caller")).toBeInTheDocument();
    expect(screen.getByText("Caller")).toBeInTheDocument();
  });

  it("keeps the open call's row visible after changing group-by", async () => {
    const user = userEvent.setup();
    renderTable({ activeCallId: "t2" });
    expect(screen.getAllByRole("row", { selected: true })).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: /Group by/ }));
    await user.click(screen.getByRole("menuitem", { name: "Status" }));
    expect(screen.getAllByRole("row", { selected: true })).toHaveLength(1);
  });

  // A row's persona name only shows while its group is open: Refund holds
  // "The Hungry Customer in a Rush", Escalate "Angry caller", Timeout "Caller".
  describe("which groups stay open", () => {
    const persona = {
      refund: () => screen.queryByText("The Hungry Customer in a Rush"),
      escalate: () => screen.queryByText("Angry caller"),
      timeout: () => screen.queryByText("Caller"),
    };
    const header = (label) => screen.getAllByText(label)[0];
    const chip = (name) => screen.getByRole("button", { name });
    const expandAll = () =>
      screen.getByRole("checkbox", { name: /Expand all/ });

    it("leaves other groups closed after opening the only group a filter showed", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(chip("Failing"));
      await user.click(header("Escalate to a human"));
      await user.click(chip(/^All/));

      expect(persona.escalate()).toBeInTheDocument();
      expect(persona.refund()).toBeNull();
      expect(persona.timeout()).toBeNull();
    });

    it("opens groups from another filter after Expand all", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(chip("Failing"));
      await user.click(expandAll());
      await user.click(chip(/^All/));

      expect(persona.refund()).toBeInTheDocument();
      expect(persona.escalate()).toBeInTheDocument();
      expect(persona.timeout()).toBeInTheDocument();
    });

    it("keeps the rest open when one group is closed after Expand all", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(expandAll());
      await user.click(header("Refund a double charge"));

      expect(persona.refund()).toBeNull();
      expect(persona.escalate()).toBeInTheDocument();
      expect(persona.timeout()).toBeInTheDocument();
      expect(expandAll()).not.toBeChecked();
    });

    it("keeps groups seen under Expand all open after one is closed elsewhere", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(chip("Failing"));
      await user.click(expandAll());
      await user.click(chip(/^All/));
      await user.click(header("Refund a double charge"));

      expect(persona.refund()).toBeNull();
      expect(persona.escalate()).toBeInTheDocument();
      expect(persona.timeout()).toBeInTheDocument();
    });

    it("reads Collapse all once every group is opened by hand, and closes them", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(header("Refund a double charge"));
      expect(expandAll()).not.toBeChecked();
      await user.click(header("Escalate to a human"));
      await user.click(header("Handle a timeout"));
      expect(expandAll()).toBeChecked();

      await user.click(expandAll());
      expect(persona.refund()).toBeNull();
      expect(persona.escalate()).toBeNull();
      expect(persona.timeout()).toBeNull();
    });

    it("keeps Expand all through a group-by change", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(expandAll());
      await user.click(screen.getByRole("button", { name: /Group by/ }));
      await user.click(screen.getByRole("menuitem", { name: "Status" }));

      expect(persona.refund()).toBeInTheDocument();
      expect(persona.escalate()).toBeInTheDocument();
      expect(persona.timeout()).toBeInTheDocument();
    });

    const groupBy = async (user, name) => {
      await user.click(screen.getByRole("button", { name: /Group by/ }));
      await user.click(screen.getByRole("menuitem", { name }));
    };

    it("keeps the groups Expand all opened after a Group by round trip", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(expandAll());
      await groupBy(user, "Status");
      await user.click(header("Failed"));
      await groupBy(user, "Use case");

      expect(persona.refund()).toBeInTheDocument();
      expect(persona.escalate()).toBeInTheDocument();
      expect(persona.timeout()).toBeInTheDocument();
    });

    it("keeps a group opened by hand after touching another Group by", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(header("Refund a double charge"));
      await groupBy(user, "Status");
      await user.click(header("Failed"));
      await groupBy(user, "Use case");

      expect(persona.refund()).toBeInTheDocument();
      expect(persona.escalate()).toBeNull();
    });

    it("closes groups under every Group by on Collapse all", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(header("Refund a double charge"));
      await groupBy(user, "Status");
      await user.click(expandAll());
      await user.click(expandAll());
      await groupBy(user, "Use case");

      expect(persona.refund()).toBeNull();
    });

    it("keeps groups first seen under Expand all open after one closes on another filter", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(chip("Failing"));
      await user.click(expandAll());
      await user.click(chip(/^All/));
      await user.click(chip(/^Errored/));
      await user.click(header("Handle a timeout"));
      await user.click(chip(/^All/));

      expect(persona.refund()).toBeInTheDocument();
      expect(persona.escalate()).toBeInTheDocument();
      expect(persona.timeout()).toBeNull();
    });

    // The table unmounts while a filter loads; the parent keeps the "already
    // opened for this call" marker so the remount doesn't reopen its group.
    it("keeps the open call's group closed after the user closes it and a filter loads", async () => {
      const user = userEvent.setup();
      let loading = false;
      const base = useRunCalls.getMockImplementation();
      useRunCalls.mockImplementation((...args) =>
        loading
          ? { ...base(...args), tasks: [], groups: [], isLoading: true }
          : base(...args),
      );
      const props = {
        executionId: "ex1",
        onOpenCall: vi.fn(),
        activeCallId: "t2",
      };
      const { rerender } = render(<RunTraceTable {...props} />);
      expect(persona.escalate()).toBeInTheDocument();

      await user.click(header("Escalate to a human"));
      expect(persona.escalate()).toBeNull();

      loading = true;
      await user.click(chip("Failing"));
      expect(screen.getByText("Loading calls…")).toBeInTheDocument();

      loading = false;
      rerender(<RunTraceTable {...props} />);
      expect(persona.escalate()).toBeNull();
    });

    it("starts other filters closed after Collapse all", async () => {
      const user = userEvent.setup();
      renderTable();
      await user.click(expandAll());
      await user.click(expandAll());
      await user.click(chip("Failing"));

      expect(persona.escalate()).toBeNull();
    });
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
    expect(screen.getByText("Showing 1–50 of 200")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Go to page 2" }));

    expect(screen.getByText("Scenario 200")).toBeInTheDocument();
    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ page: 2, limit: 50 }),
    );
    // Same pager as the Scenarios tab: the range on the left, rounded pages.
    expect(screen.getByText("Showing 51–100 of 200")).toBeInTheDocument();
  });

  it("opens a new page at the top of the table's own scroll box", async () => {
    const user = userEvent.setup();
    useRunCalls.mockImplementation((_executionId, opts = {}) => ({
      tasks: TASKS,
      columns: COLUMNS,
      groups: groupsFor(TASKS, opts.groupBy),
      facets: FACETS,
      count: 200,
      totalPages: 2,
      isLoading: false,
    }));
    const scrollTo = vi.fn();
    const original = Element.prototype.scrollTo;
    Element.prototype.scrollTo = scrollTo;
    try {
      renderTable();
      await user.click(screen.getByRole("button", { name: "Go to page 2" }));

      expect(scrollTo).toHaveBeenCalledWith({ top: 0 });
      // The box holding the table, not the card around it.
      const scrolled = scrollTo.mock.contexts.at(-1);
      expect(scrolled.querySelector(":scope > table")).not.toBeNull();
    } finally {
      Element.prototype.scrollTo = original;
    }
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

  describe("voice-only metrics on a chat run", () => {
    const withRun = (agentType, simulationCallType) => {
      const impl = useRunCalls.getMockImplementation();
      useRunCalls.mockImplementation((...args) => {
        const result = impl(...args);
        return {
          ...result,
          agentType,
          tasks: result.tasks.map((t) => ({ ...t, simulationCallType })),
          groups: result.groups.map((g) => ({
            ...g,
            rows: g.rows.map((t) => ({ ...t, simulationCallType })),
          })),
        };
      });
    };
    const VOICE_ONLY = ["AI interruptions", "Stop latency"];
    const headers = () =>
      VOICE_ONLY.map((name) => screen.queryByRole("columnheader", { name }));
    const pickerItems = async (user) => {
      await user.click(screen.getByRole("button", { name: /Columns/ }));
      return VOICE_ONLY.map((name) => screen.queryByRole("menuitem", { name }));
    };

    it("shows the columns and picker entries on a voice run", async () => {
      const user = userEvent.setup();
      withRun("voice", "voice");
      renderTable();
      headers().forEach((h) => expect(h).toBeInTheDocument());
      (await pickerItems(user)).forEach((item) =>
        expect(item).toBeInTheDocument(),
      );
    });

    it("hides the columns and picker entries on a chat run", async () => {
      const user = userEvent.setup();
      withRun("text", "text");
      renderTable();
      headers().forEach((h) => expect(h).toBeNull());
      expect(screen.getByRole("button", { name: /Columns/ })).toHaveTextContent(
        `/${TRACE_COLUMNS.length - VOICE_ONLY_COLUMNS.size}`,
      );
      (await pickerItems(user)).forEach((item) => expect(item).toBeNull());
    });

    it("falls back to the calls when the run has no agent type", () => {
      withRun(null, "text");
      renderTable();
      headers().forEach((h) => expect(h).toBeNull());
    });

    it("keeps the column when the run's type and calls are unknown", () => {
      withRun(null, undefined);
      renderTable();
      headers().forEach((h) => expect(h).toBeInTheDocument());
    });
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
      "No grouping",
    ]);
    await user.click(screen.getByRole("menuitem", { name: "Task" }));

    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ groupBy: "task" }),
    );
  });

  it("lists every call flat, without group rows, under No grouping", async () => {
    const user = userEvent.setup();
    renderTable();
    await user.click(screen.getByRole("button", { name: /Group by/ }));
    await user.click(screen.getByRole("menuitem", { name: "No grouping" }));

    expect(useRunCalls).toHaveBeenLastCalledWith(
      "ex1",
      expect.objectContaining({ groupBy: "" }),
    );
    expect(screen.queryByText(/Expand all|Collapse all/)).toBeNull();
    TASKS.forEach((task) => {
      expect(screen.getAllByText(task.scenario).length).toBeGreaterThan(0);
    });
  });

  it("has no AI filter box — it isn't wired for run calls", async () => {
    const user = userEvent.setup();
    renderTable();

    await user.click(screen.getByRole("button", { name: /Filter/ }));

    expect(screen.getByRole("tab", { name: "Basic" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Ask AI/)).toBeNull();
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
