import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";

const panelPropsSpy = vi.hoisted(() => vi.fn());

// Records every render of the filter panel and shows a dialog only while it
// is open, so a test can tell whether the toolbar mounted the panel at all.
vi.mock("../TraceFilterPanel", () => ({
  default: function TraceFilterPanelDouble(props) {
    panelPropsSpy(props);
    // eslint-disable-next-line react/prop-types
    const { open, onClose, onApply, currentFilters } = props;
    if (!open) return null;
    return (
      <div role="dialog" aria-label="Filter panel">
        <button type="button" onClick={() => onApply(currentFilters)}>
          Apply filter
        </button>
        <button type="button" onClick={onClose}>
          Close filter
        </button>
      </div>
    );
  },
}));

vi.mock("../BulkActionsBar", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ onClearSelection }) => (
    <button type="button" onClick={onClearSelection}>
      Clear selection
    </button>
  ),
}));
vi.mock("../DisplayPanel", () => ({ default: () => null }));
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

const activeFilters = [statusFilter("ERROR")];

// Mirrors the Traces and Sessions parents: a filter chip (or the chip-strip
// "+") stores itself as the panel anchor and raises the open flag; the
// toolbar's toggle clears that anchor and flips the flag.
// eslint-disable-next-line react/prop-types
function SelectedRowsHarness({ mode, onApplyExtraFilters }) {
  const [selectedCount, setSelectedCount] = useState(2);
  const [isFilterOpen, setIsFilterOpen] = useState(false);
  const [externalFilterAnchor, setExternalFilterAnchor] = useState(null);
  return (
    <>
      <button
        type="button"
        onClick={(event) => {
          setExternalFilterAnchor(event.currentTarget);
          setIsFilterOpen(true);
        }}
      >
        Status chip
      </button>
      <ObserveToolbar
        inline
        mode={mode}
        tab={mode === "traces" ? "trace" : undefined}
        selectedCount={selectedCount}
        onClearSelection={() => setSelectedCount(0)}
        graphFilters={activeFilters}
        isFilterOpen={isFilterOpen}
        externalFilterAnchor={externalFilterAnchor}
        onFilterToggle={() => {
          setExternalFilterAnchor(null);
          setIsFilterOpen((open) => !open);
        }}
        onApplyExtraFilters={onApplyExtraFilters}
      />
    </>
  );
}

describe("ObserveToolbar filter panel while rows are selected", () => {
  beforeEach(() => {
    panelPropsSpy.mockClear();
  });

  it.each(["traces", "sessions"])(
    "opens a chip's filter panel on %s while rows are selected and keeps it closed after the selection clears",
    (mode) => {
      const onApplyExtraFilters = vi.fn();
      render(
        <SelectedRowsHarness
          mode={mode}
          onApplyExtraFilters={onApplyExtraFilters}
        />,
      );

      // Rows are selected: the bulk actions bar replaces the Filter button.
      expect(
        screen.getByRole("button", { name: "Clear selection" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Filter" }),
      ).not.toBeInTheDocument();

      const chip = screen.getByRole("button", { name: "Status chip" });
      fireEvent.click(chip);
      expect(
        screen.getByRole("dialog", { name: "Filter panel" }),
      ).toBeInTheDocument();
      expect(panelPropsSpy.mock.calls.at(-1)[0].anchorEl).toBe(chip);

      fireEvent.click(screen.getByRole("button", { name: "Apply filter" }));
      expect(onApplyExtraFilters).toHaveBeenCalledTimes(1);
      expect(onApplyExtraFilters).toHaveBeenCalledWith([
        expect.objectContaining({ column_id: "status" }),
      ]);

      fireEvent.click(screen.getByRole("button", { name: "Close filter" }));
      expect(
        screen.queryByRole("dialog", { name: "Filter panel" }),
      ).not.toBeInTheDocument();

      // Clearing the selection must not pop the panel open on its own.
      fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
      expect(
        screen.getByRole("button", { name: "Filter" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("dialog", { name: "Filter panel" }),
      ).not.toBeInTheDocument();
    },
  );

  it("keeps the panel mounted but closed while rows are selected and no chip anchor is set", () => {
    // The open flag lives in URL state, so it can be true while the Filter
    // button is hidden; with no chip anchor the panel must stay closed.
    render(
      <ObserveToolbar
        inline
        tab="trace"
        selectedCount={2}
        isFilterOpen
        externalFilterAnchor={null}
        onFilterToggle={vi.fn()}
        onApplyExtraFilters={vi.fn()}
      />,
    );

    expect(panelPropsSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ open: false }),
    );
    expect(
      screen.queryByRole("dialog", { name: "Filter panel" }),
    ).not.toBeInTheDocument();
  });
});
