/* eslint-disable react/prop-types */
import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";

// A first-time SDK customer looks for "Spans" in Observe and finds no tab:
// spans sit behind Display → "Group traces by" → "Span". The trace list's
// toolbar now offers Trace and Spans one click away, as a toggle on that
// same Group-by setting (the view's groupBy and its onGroupByChange), so
// the two stay in sync. It shows only where that grouping switches the
// trace list: a project's Traces tab and a user's traces. Not on Sessions
// or Users, and not on a traces toolbar that has no grouping handler.

// Keep the real toolbar and Display panel; stub unrelated children.
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

const renderAt = (ui, route = "/dashboard/observe/project-1/llm-tracing") =>
  renderWithRouter(
    <Routes>
      <Route path="/dashboard/observe/:observeId/*" element={ui} />
      <Route path="/dashboard/users/:userId" element={ui} />
    </Routes>,
    { route },
  );

// A traces view that owns its grouping, as LLMTracingView does: the toolbar
// and the Display panel both read groupBy and call onGroupByChange.
function TracesToolbar({ onGroupByChange, ...props }) {
  const [groupBy, setGroupBy] = useState("trace");
  return (
    <ObserveToolbar
      {...baseProps}
      mode="traces"
      groupBy={groupBy}
      onGroupByChange={(key) => {
        onGroupByChange?.(key);
        setGroupBy(key);
      }}
      {...props}
    />
  );
}

const toggle = () => screen.getByRole("group", { name: "Group traces by" });
const option = (name) => within(toggle()).getByRole("button", { name });

afterEach(() => {
  window.history.replaceState({}, "", "/");
});

describe("Trace and Spans toggle in the Observe toolbar", () => {
  it.each([
    ["a project's Traces tab", "/dashboard/observe/project-1/llm-tracing"],
    ["a user's traces", "/dashboard/users/user-1"],
  ])(
    "switches the trace list to spans in one click on %s",
    async (_, route) => {
      const user = userEvent.setup();
      const onGroupByChange = vi.fn();
      renderAt(<TracesToolbar onGroupByChange={onGroupByChange} />, route);

      expect(option("Trace")).toHaveAttribute("aria-pressed", "true");
      expect(option("Spans")).toHaveAttribute("aria-pressed", "false");

      await user.click(option("Spans"));
      // The same change the Display panel's "Group traces by" → "Span" makes.
      expect(onGroupByChange).toHaveBeenCalledWith("span");
      expect(option("Spans")).toHaveAttribute("aria-pressed", "true");
      expect(option("Trace")).toHaveAttribute("aria-pressed", "false");

      // Clicking the current choice asks for nothing.
      await user.click(option("Spans"));
      expect(onGroupByChange).toHaveBeenCalledTimes(1);

      await user.click(option("Trace"));
      expect(onGroupByChange).toHaveBeenLastCalledWith("trace");
      expect(option("Trace")).toHaveAttribute("aria-pressed", "true");
    },
  );

  it("follows a Group traces by change made in the Display panel", async () => {
    const user = userEvent.setup();
    renderAt(<TracesToolbar />);
    expect(option("Trace")).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Display" }));
    await user.click(await screen.findByText("Group traces by"));
    await user.click(await screen.findByRole("menuitem", { name: "Span" }));
    await user.keyboard("{Escape}");

    await waitFor(() =>
      expect(option("Spans")).toHaveAttribute("aria-pressed", "true"),
    );
    expect(option("Trace")).toHaveAttribute("aria-pressed", "false");
  });

  // Each case also renders the Traces toolbar, which offers the toggle, so
  // "not shown" is never true just because no toolbar shows one.
  it.each([
    [
      "Sessions",
      { mode: "sessions", groupBy: "sessions", onGroupByChange: vi.fn() },
    ],
    [
      "project Users",
      { mode: "users", groupBy: "users", onGroupByChange: vi.fn() },
    ],
    // A user's trace tab (UserTraceTabV2) mounts the toolbar without a
    // grouping: Display's "Group traces by" there changes nothing.
    ["a traces toolbar with no grouping", { mode: "traces" }],
    [
      "a traces toolbar whose grouping hides Span",
      {
        mode: "traces",
        groupBy: "trace",
        onGroupByChange: vi.fn(),
        hiddenGroupByOptions: ["span"],
      },
    ],
  ])("offers no Trace and Spans toggle on %s", (_, props) => {
    const { unmount } = renderAt(<ObserveToolbar {...baseProps} {...props} />);
    expect(
      screen.queryByRole("group", { name: "Group traces by" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Spans" }),
    ).not.toBeInTheDocument();
    unmount();

    renderAt(<TracesToolbar />);
    expect(toggle()).toBeInTheDocument();
  });
});
