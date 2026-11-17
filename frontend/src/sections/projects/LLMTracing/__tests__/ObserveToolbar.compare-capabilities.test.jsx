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
      <Route path="/dashboard/users/:userId" element={toolbar} />
    </Routes>,
    { route },
  );
}

afterEach(() => {
  window.history.replaceState({}, "", "/");
});

describe("comparison capabilities", () => {
  // Project Sessions and Users draw a Compare graph: the box is enabled,
  // carries no release reason and calls the view's toggle.
  it.each(["sessions", "users"])(
    "shows a working Compare graph on project %s and keeps grouping",
    async (mode) => {
      const user = userEvent.setup();
      const onCompareToggle = vi.fn();
      renderToolbar({ mode, isCompareActive: false, onCompareToggle });

      await user.click(screen.getByRole("button", { name: "Display" }));

      expect(await screen.findByText("Group traces by")).toBeInTheDocument();
      expect(screen.getByText("GRAPH", { exact: true })).toBeInTheDocument();
      const compare = screen.getByRole("checkbox", { name: "Compare graph" });
      expect(compare).not.toHaveAttribute("aria-disabled");
      expect(compare).not.toBeChecked();
      expect(
        screen
          .getByText("Compare graph")
          .closest('[title="Not available in this release"]'),
      ).toBeNull();

      await user.click(compare);

      expect(onCompareToggle).toHaveBeenCalledOnce();
    },
  );

  // A restored compare flag on project Sessions and Users is live: the box
  // is checked and the toolbar hands date and Filter to the two graphs, as
  // on Traces.
  it.each(["sessions", "users"])(
    "hands date and Filter to the two graphs on %s with a restored compare flag",
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

      expect(
        screen.queryByRole("button", { name: "Past 7D" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Filter" }),
      ).not.toBeInTheDocument();
      expect(setDateFilter).not.toHaveBeenCalled();
      expect(onFilterToggle).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: "Display" }));
      const compare = await screen.findByRole("checkbox", {
        name: "Compare graph",
      });
      expect(compare).not.toHaveAttribute("aria-disabled");
      expect(compare).toBeChecked();
    },
  );

  it.each(["sessions", "users"])(
    "toggles the %s comparison and hands date and Filter to the graphs",
    async (mode) => {
      const user = userEvent.setup();
      function ViewToolbar() {
        const [isCompareActive, setIsCompareActive] = useState(false);
        return (
          <ObserveToolbar
            {...baseProps}
            mode={mode}
            isCompareActive={isCompareActive}
            onCompareToggle={() => setIsCompareActive((active) => !active)}
          />
        );
      }
      renderWithRouter(
        <Routes>
          <Route
            path="/dashboard/observe/:observeId/*"
            element={<ViewToolbar />}
          />
        </Routes>,
        { route: `/dashboard/observe/project-1/${mode}` },
      );

      expect(
        screen.getByRole("button", { name: "Past 7D" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Filter" }),
      ).toBeInTheDocument();
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
    },
  );

  // A view that draws no graphs (Sessions on a user's page) passes a reason:
  // the box stays listed, disabled with that reason, and a restored flag
  // hides neither date nor Filter.
  it("keeps Compare graph disabled with the view's reason and date and Filter usable", async () => {
    const user = userEvent.setup();
    const reason = "Compare graph needs a project.";
    const onCompareToggle = vi.fn();
    const setDateFilter = vi.fn();
    const onFilterToggle = vi.fn();
    renderToolbar(
      {
        mode: "sessions",
        isCompareActive: true,
        compareDisabledReason: reason,
        onCompareToggle,
        setDateFilter,
        onFilterToggle,
      },
      "/dashboard/users/user-1",
    );

    await user.click(screen.getByRole("button", { name: "Past 7D" }));
    await user.click(
      await screen.findByRole("menuitem", { name: "Yesterday" }),
    );
    expect(setDateFilter).toHaveBeenCalledOnce();
    await user.click(await screen.findByRole("button", { name: "Filter" }));
    expect(onFilterToggle).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("button", { name: "Display" }));
    const compare = await screen.findByRole("checkbox", {
      name: "Compare graph",
    });
    expect(compare).toHaveAttribute("aria-disabled", "true");
    expect(compare).not.toBeChecked();
    expect(
      screen.getByText("Compare graph").closest(`[title="${reason}"]`),
    ).not.toBeNull();
    await user.click(compare);
    expect(onCompareToggle).not.toHaveBeenCalled();
  });

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

  // The standalone users page draws no graphs: a compare flag restored there
  // leaves the toolbar's date and Filter in place.
  it("ignores a restored compare flag on the standalone users page", async () => {
    const user = userEvent.setup();
    const onFilterToggle = vi.fn();
    renderToolbar(
      { mode: "users", isCompareActive: true, onFilterToggle },
      "/dashboard/users",
    );

    expect(screen.getByRole("button", { name: "Past 7D" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Filter" }));
    expect(onFilterToggle).toHaveBeenCalledOnce();
  });
});
