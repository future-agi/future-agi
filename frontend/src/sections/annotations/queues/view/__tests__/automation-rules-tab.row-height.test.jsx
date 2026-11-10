import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { ROW_HEIGHT } from "src/theme/sizes";
import globalCss from "src/global.css?raw";
import AutomationRulesTab from "../automation-rules-tab";

// Regression for #1578: with domLayout="autoHeight" AG Grid gives the row
// area a 150px floor. With only a few rules the floor leaves blank space
// under the last row, so that row looks taller than the others.

const { gridProps, rulesState } = vi.hoisted(() => ({
  gridProps: { current: null },
  rulesState: { results: [], isLoading: false },
}));

// The tab passes a ref to AgGridReact, so the mock must take one too. A
// plain function given a ref makes React warn, and React (dev) builds that
// warning's component stack by calling the function with no props, which
// would overwrite the props captured by the real render.
function MockAgGridReact(props, ref) {
  gridProps.current = props;
  const { className, rowData } = props;
  return (
    <div ref={ref} data-testid="automation-rules-grid" className={className}>
      {(rowData || []).map((row) => (
        <div key={row.id} data-testid="automation-rules-row" />
      ))}
    </div>
  );
}

MockAgGridReact.propTypes = {
  className: PropTypes.string,
  rowData: PropTypes.array,
};

vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return { AgGridReact: forwardRef(MockAgGridReact) };
});

function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}

MockIconify.propTypes = {
  icon: PropTypes.string,
};

vi.mock("src/components/iconify", () => ({
  default: MockIconify,
}));

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgTheme: () => ({ withParams: () => ({}) }),
  useAgThemeWith: () => ({}),
}));

vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: () => null,
}));

vi.mock("../create-rule-dialog", () => ({
  default: () => null,
}));

vi.mock("../edit-rule-dialog", () => ({
  default: () => null,
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAutomationRules: () => ({
    data: { results: rulesState.results },
    isLoading: rulesState.isLoading,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
    isError: false,
    isFetchNextPageError: false,
    refetch: vi.fn(),
  }),
  useUpdateAutomationRule: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAutomationRule: () => ({ mutate: vi.fn(), isPending: false }),
  useEvaluateRule: () => ({ mutate: vi.fn(), isPending: false }),
}));

const makeRule = (id, name) => ({
  id,
  name,
  source_type: "trace",
  enabled: true,
  trigger_frequency: "manual",
  trigger_count: 0,
  last_triggered_at: null,
});

describe("AutomationRulesTab row height (#1578)", () => {
  beforeEach(() => {
    gridProps.current = null;
    rulesState.results = [];
    rulesState.isLoading = false;
  });

  it("drops the auto-height floor when rules exist, so every row is the same height", () => {
    rulesState.results = [
      makeRule("rule-1", "rule 2"),
      makeRule("rule-2", "test rule"),
    ];

    render(<AutomationRulesTab queueId="queue-1" queue={{ id: "queue-1" }} />);

    expect(screen.getAllByTestId("automation-rules-row")).toHaveLength(2);
    expect(screen.getByTestId("automation-rules-grid")).toHaveClass(
      "no-min-height-grid",
    );
    expect(gridProps.current.domLayout).toBe("autoHeight");
    expect(gridProps.current.rowHeight).toBe(ROW_HEIGHT.default);
    expect(gridProps.current.getRowHeight).toBeUndefined();
  });

  it("keeps the floor while there are no rules, so the empty state has room", () => {
    render(<AutomationRulesTab queueId="queue-1" queue={{ id: "queue-1" }} />);

    expect(screen.getByTestId("automation-rules-grid")).not.toHaveClass(
      "no-min-height-grid",
    );
  });

  it("removes the floor from both the row viewport and the row container", () => {
    const blocks = globalCss.match(/[^{}]*\{[^{}]*\}/g) || [];
    const rule = blocks.find(
      (block) =>
        block.includes(".no-min-height-grid") &&
        /min-height:\s*unset\s*!important/.test(block),
    );

    expect(rule).toBeDefined();
    expect(rule).toMatch(/\.no-min-height-grid\s+\.ag-center-cols-viewport/);
    expect(rule).toMatch(/\.no-min-height-grid\s+\.ag-center-cols-container/);
  });
});
