import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import DisplayPanel from "../DisplayPanel";

vi.mock("src/components/iconify", () => ({
  default: () => null,
}));

const baseProps = {
  anchorEl: document.createElement("button"),
  open: true,
  onClose: vi.fn(),
  mode: "traces",
  viewMode: "graph",
  cellHeight: "Short",
};

describe("DisplayPanel — Add custom columns badge (TH-4139)", () => {
  it("shows '2 added' when there are 2 unique custom columns", () => {
    const columns = [
      { id: "trace_name" },
      { id: "llm.token_count.prompt", groupBy: "Custom Columns" },
      {
        id: "gen_ai.output.messages.0.message.role",
        groupBy: "Custom Columns",
      },
    ];
    render(<DisplayPanel {...baseProps} columns={columns} />);
    expect(screen.getByText("2 added")).toBeInTheDocument();
  });

  it("dedupes by id so duplicate custom columns count once", () => {
    const columns = [
      { id: "trace_name" },
      { id: "llm.token_count.prompt", groupBy: "Custom Columns" },
      { id: "llm.token_count.prompt", groupBy: "Custom Columns" },
    ];
    render(<DisplayPanel {...baseProps} columns={columns} />);
    expect(screen.getByText("1 added")).toBeInTheDocument();
  });

  it("hides the badge when no custom columns are added", () => {
    const columns = [{ id: "trace_name" }, { id: "input" }];
    render(<DisplayPanel {...baseProps} columns={columns} />);
    expect(screen.queryByText(/added/)).not.toBeInTheDocument();
  });
});

describe("DisplayPanel — interactive controls", () => {
  it("wires each non-destructive display control to its owner", async () => {
    const user = userEvent.setup();
    const onViewModeChange = vi.fn();
    const setCellHeight = vi.fn();
    const onColumnVisibilityChange = vi.fn();
    const onAutoSize = vi.fn();
    const onAddCustomColumn = vi.fn();
    const onToggleEvalFilter = vi.fn();
    const onToggleErrors = vi.fn();
    const onToggleNonAnnotated = vi.fn();
    const onGroupByChange = vi.fn();
    const onCompareToggle = vi.fn();
    const onResetView = vi.fn();
    const onClose = vi.fn();

    render(
      <DisplayPanel
        {...baseProps}
        onClose={onClose}
        columns={[{ id: "trace_name", isVisible: true }]}
        onViewModeChange={onViewModeChange}
        setCellHeight={setCellHeight}
        onColumnVisibilityChange={onColumnVisibilityChange}
        onAutoSize={onAutoSize}
        onAddCustomColumn={onAddCustomColumn}
        showEvalToggle
        onToggleEvalFilter={onToggleEvalFilter}
        onToggleErrors={onToggleErrors}
        onToggleNonAnnotated={onToggleNonAnnotated}
        groupBy="trace"
        onGroupByChange={onGroupByChange}
        onCompareToggle={onCompareToggle}
        onResetView={onResetView}
      />,
    );

    await user.click(screen.getByText("Agent Graph"));
    expect(onViewModeChange).toHaveBeenNthCalledWith(1, "agentGraph");
    await user.click(screen.getByText("Agent Path"));
    expect(onViewModeChange).toHaveBeenNthCalledWith(2, "agentPath");

    await user.click(screen.getByText("Row height"));
    await user.click(screen.getByText("Medium"));
    expect(setCellHeight).toHaveBeenCalledWith("Medium");

    await user.click(screen.getByText("View columns"));
    await user.click(screen.getByText("Autosize columns"));
    await user.click(screen.getByText("Add custom columns"));
    expect(onColumnVisibilityChange).toHaveBeenCalledOnce();
    expect(onAutoSize).toHaveBeenCalledOnce();
    expect(onAddCustomColumn).toHaveBeenCalledOnce();

    await user.click(screen.getByText("Show traces with evals"));
    await user.click(screen.getByText("Errors"));
    await user.click(screen.getByText("Non annotated"));
    await user.click(screen.getByText("Compare graph"));
    expect(onToggleEvalFilter).toHaveBeenCalledOnce();
    expect(onToggleErrors).toHaveBeenCalledOnce();
    expect(onToggleNonAnnotated).toHaveBeenCalledOnce();
    expect(onCompareToggle).toHaveBeenCalledOnce();

    await user.click(screen.getByText("Group traces by"));
    await user.click(screen.getByText("Sessions"));
    expect(onGroupByChange).toHaveBeenCalledWith("sessions");

    await user.click(screen.getByText("Reset"));
    expect(onResetView).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("disables agent visualizations for voice projects", () => {
    const onViewModeChange = vi.fn();
    render(
      <DisplayPanel
        {...baseProps}
        isSimulator
        onViewModeChange={onViewModeChange}
      />,
    );

    expect(screen.getByText("Agent Graph").closest("button")).toBeDisabled();
    expect(screen.getByText("Agent Path").closest("button")).toBeDisabled();
    expect(onViewModeChange).not.toHaveBeenCalled();
  });

  it("disables agent graph until cross-project user detail selects a project", () => {
    render(<DisplayPanel {...baseProps} agentGraphEnabled={false} />);

    expect(screen.getByText("Agent Graph").closest("button")).toBeDisabled();
    expect(screen.getByText("Agent Path").closest("button")).toBeDisabled();
  });
});
