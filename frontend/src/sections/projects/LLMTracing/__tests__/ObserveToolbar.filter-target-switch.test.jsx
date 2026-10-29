import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";

const traceFilterPanelPropsMock = vi.hoisted(() => vi.fn());

vi.mock("../TraceFilterPanel", () => ({
  default: (props) => {
    traceFilterPanelPropsMock(props);
    return null;
  },
}));

vi.mock("../DisplayPanel", () => ({ default: () => null }));
vi.mock("../BulkActionsBar", () => ({ default: () => null }));
vi.mock("../tabStore", () => ({
  useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));

const statusFilter = (value) => ({
  column_id: "status",
  display_name: "Status",
  filter_config: {
    col_type: "SYSTEM_METRIC",
    filter_type: "text",
    filter_op: "in",
    filter_value: [value],
  },
});

const toolbar = (props) => (
  <ObserveToolbar
    inline
    tab="trace"
    onFilterToggle={vi.fn()}
    onApplyExtraFilters={vi.fn()}
    onApplyCompareExtraFilters={vi.fn()}
    {...props}
  />
);

// TraceFilterPanel seeds its rows from currentFilters only on the render where
// `open` turns true, so that render must already carry the target's filters.
const currentFiltersWhenOpened = () =>
  traceFilterPanelPropsMock.mock.calls.find(([props]) => props.open)[0]
    .currentFilters;

describe("ObserveToolbar filter panel target switch", () => {
  beforeEach(() => {
    traceFilterPanelPropsMock.mockClear();
  });

  it("opens on the compare graph's filters when the target switches to compare", () => {
    const anchor = document.createElement("button");
    const { rerender } = render(
      toolbar({
        filterTarget: "primary",
        graphFilters: [],
        isFilterOpen: false,
      }),
    );
    rerender(
      toolbar({
        filterTarget: "compare",
        graphFilters: [statusFilter("OK")],
        isFilterOpen: true,
        externalFilterAnchor: anchor,
      }),
    );
    expect(currentFiltersWhenOpened()).toEqual([
      expect.objectContaining({ field: "status", value: ["OK"] }),
    ]);
  });

  it("opens on the primary graph's filters when the target switches back to primary", () => {
    const anchor = document.createElement("button");
    const { rerender } = render(
      toolbar({
        filterTarget: "compare",
        graphFilters: [statusFilter("ERROR")],
        isFilterOpen: false,
      }),
    );
    rerender(
      toolbar({
        filterTarget: "primary",
        graphFilters: [statusFilter("OK")],
        isFilterOpen: true,
        externalFilterAnchor: anchor,
      }),
    );
    expect(currentFiltersWhenOpened()).toEqual([
      expect.objectContaining({ field: "status", value: ["OK"] }),
    ]);
  });
});
