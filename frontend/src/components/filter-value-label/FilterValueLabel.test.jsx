import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import FilterValueLabel from "./FilterValueLabel";

const DEFAULT_OPTIONS = [
  { value: "p1", label: "Project Alpha" },
  { value: "p2", label: "Project Beta" },
  { value: "p3", label: "Project Gamma" },
];

// FilterValueLabel resolves ids -> names via useDashboardFilterValues; mock it
// with a controllable result and drive behavior through the `filter` prop.
const { mockState, hookSpy } = vi.hoisted(() => ({
  mockState: { current: { data: [], isLoading: false } },
  hookSpy: vi.fn(),
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: (args) => {
    hookSpy(args);
    return mockState.current;
  },
}));

const baseFilter = { name: "Project", type: "system", id: "project_id" };
const renderLabel = (filter, extra = {}) =>
  render(<FilterValueLabel filter={filter} source="traces" {...extra} />);

describe("FilterValueLabel", () => {
  beforeEach(() => {
    mockState.current = { data: DEFAULT_OPTIONS, isLoading: false };
    hookSpy.mockClear();
  });

  // Custom-attribute labels are always the value, so resolving one costs a
  // workspace-wide span scan and returns nothing new — and because that list
  // is time-windowed, an older value would miss the lookup and fall back to
  // the raw value anyway.
  it("renders custom-attribute values without fetching the value list", () => {
    renderLabel({
      name: "Model",
      type: "custom_attribute",
      id: "gen_ai.request.model",
      value: ["gpt-4o-mini"],
    });
    expect(screen.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(hookSpy).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: false }),
    );
  });

  it("still fetches for system fields, which can relabel", () => {
    renderLabel({ ...baseFilter, value: ["p1"] });
    expect(screen.getByText("Project Alpha")).toBeInTheDocument();
    expect(hookSpy).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: true }),
    );
  });

  it("shows the placeholder when nothing is selected", () => {
    renderLabel({ ...baseFilter, value: [] });
    expect(screen.getByText("Select value...")).toBeInTheDocument();
  });

  it("shows the option name and no badge when one is selected", () => {
    renderLabel({ ...baseFilter, value: ["p1"] });
    expect(screen.getByText("Project Alpha")).toBeInTheDocument();
    expect(screen.queryByText(/^\+\d/)).not.toBeInTheDocument();
  });

  it("shows a singular badge for two selected", () => {
    renderLabel({ ...baseFilter, value: ["p1", "p2"] });
    expect(screen.getByText("Project Alpha")).toBeInTheDocument();
    expect(screen.getByText("+1 project")).toBeInTheDocument();
  });

  it("shows a pluralized badge for several selected", () => {
    renderLabel({ ...baseFilter, value: ["p1", "p2", "p3"] });
    expect(screen.getByText("Project Alpha")).toBeInTheDocument();
    expect(screen.getByText("+2 projects")).toBeInTheDocument();
  });

  it("pluralizes field names ending in s/y correctly", () => {
    const { unmount } = renderLabel({
      ...baseFilter,
      name: "Status",
      value: ["p1", "p2", "p3"],
    });
    expect(screen.getByText("+2 statuses")).toBeInTheDocument();
    unmount();

    renderLabel({ ...baseFilter, name: "Category", value: ["p1", "p2", "p3"] });
    expect(screen.getByText("+2 categories")).toBeInTheDocument();
  });

  it("falls back to the raw value when no label matches", () => {
    renderLabel({ ...baseFilter, value: ["unknown-id"] });
    expect(screen.getByText("unknown-id")).toBeInTheDocument();
  });

  it("shows a skeleton instead of raw ids while resolving", () => {
    mockState.current = { data: [], isLoading: true };
    const { container } = renderLabel({ ...baseFilter, value: ["p1", "p2"] });
    expect(container.querySelector(".MuiSkeleton-root")).toBeInTheDocument();
    expect(screen.queryByText("p1")).not.toBeInTheDocument();
    expect(screen.queryByText(/^\+\d/)).not.toBeInTheDocument();
  });

  it("calls onClick when the row is clicked", async () => {
    const onClick = vi.fn();
    renderLabel({ ...baseFilter, value: ["p1"] }, { onClick });
    await userEvent.click(screen.getByText("Project Alpha"));
    expect(onClick).toHaveBeenCalled();
  });

  it("lists all selected names in the tooltip on hover", async () => {
    renderLabel({ ...baseFilter, value: ["p1", "p2", "p3"] });
    await userEvent.hover(screen.getByText("Project Alpha"));
    expect(await screen.findByText("Project Beta")).toBeInTheDocument();
    expect(await screen.findByText("Project Gamma")).toBeInTheDocument();
  });
});
