import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithRouter, screen, waitFor } from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";

// Keep the real toolbar and DisplayPanel; only unrelated children are stubbed.
vi.mock("../TraceFilterPanel", () => ({ default: () => null }));
vi.mock("../BulkActionsBar", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../tabStore", () => ({
  useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }),
}));

const baseProps = {
  inline: true,
  dateLabel: "Past 7D",
  dateFilter: { dateOption: "7D" },
  columns: [],
  cellHeight: "Short",
  viewMode: "graph",
  selectedCount: 0,
  isFilterOpen: false,
};

function renderToolbar(
  props,
  route = `/dashboard/observe/project-1/${props.mode}`,
) {
  const toolbar = <ObserveToolbar {...baseProps} {...props} />;
  return renderWithRouter(
    <Routes>
      <Route path="/dashboard/observe/:observeId/*" element={toolbar} />
      <Route path="/dashboard/users" element={toolbar} />
    </Routes>,
    { route },
  );
}

afterEach(() => {
  window.history.replaceState({}, "", "/");
});

describe("H12-C25-002 comparison capabilities", () => {
  // Compare graph stays listed on project Sessions and Users, but disabled
  // with the reason: it toggles nothing and never hides date or Filter.
  it.each(["sessions", "users"])(
    "shows Compare graph disabled with the reason on project %s and keeps grouping",
    async (mode) => {
      const user = userEvent.setup();
      const onCompareToggle = vi.fn();
      renderToolbar({ mode, isCompareActive: false, onCompareToggle });

      await user.click(screen.getByRole("button", { name: "Display" }));

      expect(await screen.findByText("Group traces by")).toBeInTheDocument();
      expect(screen.getByText("GRAPH", { exact: true })).toBeInTheDocument();
      const compare = screen.getByRole("checkbox", { name: "Compare graph" });
      expect(compare).toHaveAttribute("aria-disabled", "true");
      expect(compare).not.toBeChecked();
      expect(
        screen
          .getByText("Compare graph")
          .closest('[title="Not available in this release"]'),
      ).not.toBeNull();

      await user.click(compare);

      expect(onCompareToggle).not.toHaveBeenCalled();
      expect(compare).not.toBeChecked();
    },
  );

  it.each(["sessions", "users"])(
    "keeps date and filter controls usable on %s with a restored compare flag",
    async (mode) => {
      const user = userEvent.setup();
      const setDateFilter = vi.fn();
      const onFilterToggle = vi.fn();
      renderToolbar({
        mode,
        isCompareActive: true,
        onCompareToggle: vi.fn(),
        setDateFilter,
        onFilterToggle,
      });

      await user.click(screen.getByRole("button", { name: "Past 7D" }));
      await user.click(
        await screen.findByRole("menuitem", { name: "Yesterday" }),
      );
      expect(setDateFilter).toHaveBeenCalledOnce();
      expect(
        setDateFilter.mock.calls[0][0](baseProps.dateFilter).dateOption,
      ).toBe("Yesterday");

      await user.click(await screen.findByRole("button", { name: "Filter" }));
      expect(onFilterToggle).toHaveBeenCalledOnce();
      await user.click(screen.getByRole("button", { name: "Display" }));
      // A restored compare flag does not check the disabled box.
      const compare = await screen.findByRole("checkbox", {
        name: "Compare graph",
      });
      expect(compare).toHaveAttribute("aria-disabled", "true");
      expect(compare).not.toBeChecked();
    },
  );

  it("preserves the Traces comparison toggle and toolbar control handoff", async () => {
    const user = userEvent.setup();
    function TracesToolbar() {
      const [isCompareActive, setIsCompareActive] = useState(false);
      return (
        <ObserveToolbar
          {...baseProps}
          mode="traces"
          isCompareActive={isCompareActive}
          onCompareToggle={() => setIsCompareActive((active) => !active)}
        />
      );
    }
    renderWithRouter(
      <Routes>
        <Route
          path="/dashboard/observe/:observeId/llm-tracing"
          element={<TracesToolbar />}
        />
      </Routes>,
      { route: "/dashboard/observe/project-1/llm-tracing" },
    );

    expect(screen.getByRole("button", { name: "Past 7D" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Filter" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Display" }));
    await user.click(
      await screen.findByRole("checkbox", { name: "Compare graph" }),
    );
    expect(
      screen.getByRole("checkbox", { name: "Compare graph" }),
    ).toBeChecked();
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Display" })).toBeVisible();
    });
    expect(
      screen.queryByRole("button", { name: "Past 7D" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Filter" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Display" }));
    await user.click(
      await screen.findByRole("checkbox", { name: "Compare graph" }),
    );
    expect(
      screen.getByRole("checkbox", { name: "Compare graph" }),
    ).not.toBeChecked();
    await user.keyboard("{Escape}");
    expect(
      await screen.findByRole("button", { name: "Past 7D" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Filter" })).toBeVisible();
  });

  it("keeps standalone Users grouping and comparison hidden", async () => {
    const user = userEvent.setup();
    renderToolbar(
      { mode: "users", isCompareActive: false },
      "/dashboard/users",
    );

    await user.click(screen.getByRole("button", { name: "Display" }));
    expect(screen.queryByText("Group traces by")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "Compare graph" }),
    ).not.toBeInTheDocument();
  });
});
