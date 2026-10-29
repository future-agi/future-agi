import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";

const datePickerProps = vi.hoisted(() => ({ current: null }));

vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: (props) => {
    datePickerProps.current = props;
    return null;
  },
}));
vi.mock("../TraceFilterPanel", () => ({ default: () => null }));
vi.mock("../DisplayPanel", () => ({ default: () => null }));
vi.mock("../BulkActionsBar", () => ({ default: () => null }));
vi.mock("../tabStore", () => ({
  useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

describe("ObserveToolbar custom date range", () => {
  it("opens the custom range picker on the range the view has applied", async () => {
    const applied = ["2026-10-09 00:00:00", "2026-10-17 00:00:00"];
    render(
      <ObserveToolbar
        inline
        tab="trace"
        dateLabel="09/10/2026 - 17/10/2026"
        dateFilter={{ dateOption: "Custom", dateFilter: applied }}
        setDateFilter={vi.fn()}
        isFilterOpen={false}
        onFilterToggle={vi.fn()}
        onApplyExtraFilters={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /09\/10\/2026 - 17\/10\/2026/ }),
    );
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Custom range" }),
    );
    expect(datePickerProps.current.open).toBe(true);
    expect(datePickerProps.current.value).toEqual(applied);
  });
});
