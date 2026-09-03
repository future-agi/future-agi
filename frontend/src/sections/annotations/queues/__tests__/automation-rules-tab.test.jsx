import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AutomationRulesTab from "../view/automation-rules-tab";

const {
  mockUseAutomationRules,
  mockUseUpdateAutomationRule,
  mockUseDeleteAutomationRule,
  mockUseEvaluateRule,
  mockEvaluateRule,
} = vi.hoisted(() => ({
  mockUseAutomationRules: vi.fn(),
  mockUseUpdateAutomationRule: vi.fn(() => ({ mutate: vi.fn() })),
  mockUseDeleteAutomationRule: vi.fn(() => ({ mutate: vi.fn() })),
  mockUseEvaluateRule: vi.fn(),
  mockEvaluateRule: vi.fn(),
}));

function MockAgGridReact({ rowData, columnDefs, context }) {
  return (
    <div data-testid="ag-grid">
      {(rowData || []).map((row) => (
        <div key={row.id} data-testid="ag-grid-row">
          {columnDefs.map((col) => {
            const Renderer = col.cellRenderer;
            return Renderer ? (
              <div key={col.field}>
                <Renderer data={row} context={context} />
              </div>
            ) : null;
          })}
        </div>
      ))}
    </div>
  );
}

MockAgGridReact.propTypes = {
  rowData: PropTypes.array,
  columnDefs: PropTypes.array.isRequired,
  context: PropTypes.object,
};

vi.mock("ag-grid-react", () => ({
  AgGridReact: MockAgGridReact,
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => ({}),
}));

vi.mock("src/styles/clean-data-table.css", () => ({}));

vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: () => null,
}));

vi.mock("../view/create-rule-dialog", () => ({
  default: () => null,
}));

vi.mock("../view/edit-rule-dialog", () => ({
  default: () => null,
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAutomationRules: mockUseAutomationRules,
  useUpdateAutomationRule: mockUseUpdateAutomationRule,
  useDeleteAutomationRule: mockUseDeleteAutomationRule,
  useEvaluateRule: mockUseEvaluateRule,
}));

describe("AutomationRulesTab", () => {
  it("only disables the rule that is currently evaluating", async () => {
    const user = userEvent.setup();
    mockUseAutomationRules.mockReturnValue({
      isLoading: false,
      data: {
        results: [
          {
            id: "rule-1",
            name: "Rule One",
            source_type: "trace",
            enabled: true,
            trigger_frequency: "manual",
            trigger_count: 0,
          },
          {
            id: "rule-2",
            name: "Rule Two",
            source_type: "trace",
            enabled: true,
            trigger_frequency: "manual",
            trigger_count: 0,
          },
        ],
      },
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      isFetchingNextPage: false,
    });
    const pendingByRow = [true, false];
    let call = 0;
    mockUseEvaluateRule.mockImplementation(() => ({
      mutate: mockEvaluateRule,
      isPending: pendingByRow[call++ % pendingByRow.length],
    }));

    render(<AutomationRulesTab queueId="queue-1" queue={{ id: "queue-1" }} />);

    expect(screen.getByRole("button", { name: /running/i })).toBeDisabled();
    expect(screen.getByText("Rule Two")).toBeInTheDocument();

    const runNow = screen.getByRole("button", { name: /^run now$/i });
    expect(runNow).toBeEnabled();

    await user.click(runNow);

    expect(mockEvaluateRule).toHaveBeenCalledWith({
      queueId: "queue-1",
      ruleId: "rule-2",
    });
  });

  it("shows an explicit bounded Load more control", async () => {
    const user = userEvent.setup();
    const fetchNextPage = vi.fn();
    mockUseAutomationRules.mockReturnValue({
      isLoading: false,
      data: {
        results: [
          {
            id: "rule-1",
            name: "Rule One",
            source_type: "trace",
            enabled: true,
            trigger_frequency: "manual",
            trigger_count: 0,
          },
        ],
      },
      hasNextPage: true,
      fetchNextPage,
      isFetchingNextPage: false,
    });
    mockUseEvaluateRule.mockReturnValue({
      mutate: mockEvaluateRule,
      isPending: false,
    });

    render(<AutomationRulesTab queueId="queue-1" queue={{ id: "queue-1" }} />);

    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });

  it("shows an initial failure instead of the no-rules state and retries explicitly", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    mockUseAutomationRules.mockReturnValue({
      isLoading: false,
      data: undefined,
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      isFetchingNextPage: false,
      isError: true,
      isFetchNextPageError: false,
      refetch,
    });
    mockUseEvaluateRule.mockReturnValue({
      mutate: mockEvaluateRule,
      isPending: false,
    });

    render(<AutomationRulesTab queueId="queue-1" queue={{ id: "queue-1" }} />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "We couldn't load automation rules",
    );
    expect(
      screen.queryByText("No automation rules configured."),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("keeps prior rows and retries only the failed next page", async () => {
    const user = userEvent.setup();
    const fetchNextPage = vi.fn();
    const refetch = vi.fn();
    mockUseAutomationRules.mockReturnValue({
      isLoading: false,
      data: {
        results: [
          {
            id: "rule-1",
            name: "Rule One",
            source_type: "trace",
            enabled: true,
            trigger_frequency: "manual",
            trigger_count: 0,
          },
        ],
      },
      hasNextPage: true,
      fetchNextPage,
      isFetchingNextPage: false,
      isError: true,
      isFetchNextPageError: true,
      refetch,
    });
    mockUseEvaluateRule.mockReturnValue({
      mutate: mockEvaluateRule,
      isPending: false,
    });

    render(<AutomationRulesTab queueId="queue-1" queue={{ id: "queue-1" }} />);

    expect(screen.getByText("Rule One")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
    expect(refetch).not.toHaveBeenCalled();
  });
});
