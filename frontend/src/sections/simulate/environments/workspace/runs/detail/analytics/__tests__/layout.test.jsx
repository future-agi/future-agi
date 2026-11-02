import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cloneElement } from "react";
import {
  act,
  fireEvent,
  render,
  renderHook,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { AGENT_TYPES, chatAnalytics, voiceAnalytics } from "./analyticsFixture";

const useRunAnalytics = vi.fn();
const downloadCsv = vi.fn();
const enqueueSnackbar = vi.fn();
vi.mock("recharts", async (importOriginal) => ({
  ...(await importOriginal()),
  ResponsiveContainer: ({ children }) =>
    cloneElement(children, { width: 600, height: 250 }),
}));
vi.mock("src/api/simulate-environments/runAnalytics", () => ({
  useRunAnalytics: (...args) => useRunAnalytics(...args),
}));
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useRunDetail: () => ({ identity: { ordinal: 4 }, isLoading: false }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => enqueueSnackbar(...args),
}));
vi.mock("../csv", async (importOriginal) => ({
  ...(await importOriginal()),
  downloadCsv: (...args) => downloadCsv(...args),
}));

const { default: RunAnalytics } = await import("../../RunAnalytics");
const { toCsv } = await import("../csv");
const { resetLayouts, useRunLayout } = await import("../useRunLayout");

let setItem;
const ownWrites = (spy) =>
  spy.mock.calls.filter(([key]) => !String(key).startsWith("iconify"));
const show = (data, { envId = "env-1", route } = {}) => {
  useRunAnalytics.mockReturnValue({ data, isPending: false, isError: false });
  const ui = <RunAnalytics executionId="execution-1" envId={envId} />;
  return route ? renderWithRouter(ui, { route }) : render(ui);
};
const region = (name) => screen.getByRole("region", { name });
const openMenu = (title) =>
  fireEvent.click(screen.getByRole("button", { name: `${title} actions` }));
const pick = (name) => fireEvent.click(screen.getByRole("menuitem", { name }));

describe("analytics widget layout", () => {
  beforeEach(() => {
    resetLayouts();
    setItem = vi.spyOn(Storage.prototype, "setItem");
    window.history.replaceState({}, "", "/");
    useRunAnalytics.mockReset();
    downloadCsv.mockReset();
    enqueueSnackbar.mockReset();
  });
  afterEach(() => {
    // Nothing about the layout is written to the browser (Iconify caches
    // its icons there on its own).
    expect(ownWrites(setItem)).toEqual([]);
    setItem.mockRestore();
  });

  it.each(AGENT_TYPES)(
    "widget menu offers Edit, Duplicate, Reset, Width, Download CSV, Delete (%s)",
    (_type, fixture) => {
      show(fixture());
      openMenu("Weakest scenarios");
      const menu = screen.getByRole("menu");
      const items = within(menu)
        .getAllByRole("menuitem")
        .map((item) => item.textContent);
      expect(items).toEqual([
        "Edit",
        "Duplicate",
        "Reset to default",
        "Half width",
        "Full width",
        "Download CSV",
        "Delete",
      ]);
      // Nothing to reset on a widget that was never changed.
      expect(
        screen.getByRole("menuitem", { name: "Reset to default" }),
      ).toHaveAttribute("aria-disabled", "true");

      // Edit opens the widget editor page.
      pick("Edit");
      expect(window.location.pathname).toMatch(/\/widgets\/risk$/);
      expect(enqueueSnackbar).not.toHaveBeenCalled();

      // The copy lands right after its source, in the source's section,
      // and is not a change to the layout.
      openMenu("Weakest scenarios");
      pick("Duplicate");
      const source = region("Weakest scenarios");
      const copy = region("Weakest scenarios (copy)");
      const section = source.closest("[data-section-id]");
      expect(section).toHaveAttribute("data-section-id", "failure_analysis");
      const regions = within(section).getAllByRole("region");
      expect(regions.indexOf(copy)).toBe(regions.indexOf(source) + 1);
      expect(
        screen.queryByRole("heading", { name: "Custom widgets" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Modified")).not.toBeInTheDocument();

      openMenu("Weakest scenarios");
      pick("Half width");
      expect(screen.getByLabelText("Modified")).toBeInTheDocument();
      openMenu("Weakest scenarios");
      expect(
        screen.getByRole("menuitem", { name: "Reset to default" }),
      ).not.toHaveAttribute("aria-disabled");
    },
  );

  it.each(AGENT_TYPES)(
    "Download CSV holds the widget's real numbers (%s)",
    (_type, fixture) => {
      show(fixture());
      openMenu("Weakest scenarios");
      pick("Download CSV");
      expect(downloadCsv).toHaveBeenLastCalledWith("weakest-scenarios.csv", [
        {
          scenario: "Handle a refund",
          queued: 0,
          in_progress: 0,
          passed: 2,
          failed: 1,
          error: 0,
          inconclusive: 1,
        },
      ]);

      openMenu("Evaluations");
      pick("Download CSV");
      expect(downloadCsv).toHaveBeenLastCalledWith("evaluations.csv", [
        {
          eval: "Policy adherence",
          pass_rate: 60,
          passed: 3,
          evaluated: 5,
          could_not_run: 1,
          not_applicable: 0,
        },
      ]);

      const duration = _type === "chat" ? "Chat duration" : "Call duration";
      openMenu(duration);
      pick("Download CSV");
      expect(downloadCsv).toHaveBeenLastCalledWith(
        `${duration.toLowerCase().replace(" ", "-")}.csv`,
        [
          { [_type === "chat" ? "chat" : "call"]: "1", duration_ms: 189000 },
          { [_type === "chat" ? "chat" : "call"]: "2", duration_ms: null },
          { [_type === "chat" ? "chat" : "call"]: "3", duration_ms: 308100 },
        ],
      );

      // A widget with nothing measured has nothing to download.
      openMenu("Failure attribution");
      expect(
        screen.getByRole("menuitem", { name: "Download CSV" }),
      ).toHaveAttribute("aria-disabled", "true");
    },
  );

  it("a width set back to its default is not a change", () => {
    show(voiceAnalytics());
    openMenu("Goal outcome breakdown");
    pick("Half width");
    expect(screen.getByLabelText("Modified")).toBeInTheDocument();

    openMenu("Goal outcome breakdown");
    pick("Quarter width");
    expect(screen.queryByLabelText("Modified")).not.toBeInTheDocument();
    openMenu("Goal outcome breakdown");
    expect(
      screen.getByRole("menuitem", { name: "Reset to default" }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("a widget moved back to its own section is not a change", () => {
    const { result } = renderHook(() =>
      useRunLayout({ envId: "env-1", surface: "voice" }),
    );
    act(() =>
      result.current.reorderAndSection(
        result.current.visibleIds,
        "goal_outcome",
        "reliability",
      ),
    );
    expect(result.current.hasOverrides("goal_outcome")).toBe(true);
    act(() =>
      result.current.reorderAndSection(
        result.current.visibleIds,
        "goal_outcome",
        "breakdowns",
      ),
    );
    expect(result.current.hasOverrides("goal_outcome")).toBe(false);
  });

  it("writes CSV with quoted cells", () => {
    expect(
      toCsv([
        { scenario: 'Refund, "late"', count: 2 },
        { scenario: "Cancel", count: null },
      ]),
    ).toBe('scenario,count\n"Refund, ""late""",2\nCancel,');
  });

  it.each(AGENT_TYPES)(
    "Delete hides a built-in and Hidden (N) restores it (%s)",
    async (_type, fixture) => {
      const slowest = _type === "chat" ? "Slowest chats" : "Slowest calls";
      show(fixture());
      openMenu(slowest);
      pick("Delete");
      expect(screen.queryByRole("region", { name: slowest })).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Hidden (1)" }));
      const popover = screen
        .getByText("Hidden widgets")
        .closest(".MuiPaper-root");
      expect(within(popover).getByText(slowest)).toBeVisible();
      fireEvent.click(within(popover).getByRole("button", { name: "Show" }));
      expect(
        within(popover).getByText("Nothing is hidden in this view."),
      ).toBeVisible();
      fireEvent.keyDown(popover, { key: "Escape" });

      await waitFor(() => expect(region(slowest)).toBeInTheDocument());
      expect(screen.getByRole("button", { name: "Hidden" })).toBeVisible();
    },
  );

  it("Reset layout asks first", async () => {
    show(voiceAnalytics());
    openMenu("Slowest calls");
    pick("Delete");
    fireEvent.click(screen.getByRole("button", { name: "Hidden (1)" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset layout" }));

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Reset this view to the default layout?"),
    ).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("region", { name: "Slowest calls" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Hidden (1)" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset layout" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Reset",
      }),
    );
    await waitFor(() => expect(region("Slowest calls")).toBeInTheDocument());
  });

  it("views can't be saved yet: Save view is disabled and flagged UI only", async () => {
    show(chatAnalytics());
    const save = screen.getByRole("button", { name: "Save view" });
    expect(save).toBeDisabled();
    fireEvent.mouseOver(save.parentElement);
    expect(
      await screen.findByText("Saving views isn't available yet."),
    ).toBeInTheDocument();

    // The pill sits right after Save view, with the views it labels.
    expect(save.parentElement.nextElementSibling).toHaveTextContent("UI only");
    fireEvent.mouseOver(screen.getByText("UI only"));
    expect(
      await screen.findByText(
        /layout changes last until you reload\. Views can't be saved yet\./,
      ),
    ).toBeInTheDocument();
  });

  it("the layout lasts for the visit and is back to default after a reload", () => {
    const first = show(chatAnalytics(), { envId: "env-a" });
    openMenu("Slowest chats");
    pick("Delete");
    first.unmount();

    // Back on the page in the same visit: still hidden.
    const again = show(chatAnalytics(), { envId: "env-a" });
    expect(screen.queryByRole("region", { name: "Slowest chats" })).toBeNull();
    expect(screen.getByRole("button", { name: "Hidden (1)" })).toBeVisible();
    again.unmount();

    // Another environment has its own layout.
    const other = show(chatAnalytics(), { envId: "env-b" });
    expect(region("Slowest chats")).toBeInTheDocument();
    other.unmount();

    // A reload starts from the default layout.
    resetLayouts();
    show(chatAnalytics(), { envId: "env-a" });
    expect(region("Slowest chats")).toBeInTheDocument();
  });

  it("a view named in the link that doesn't exist opens Default", async () => {
    show(chatAnalytics(), { route: "/?view=Ops" });
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(region("Slowest chats")).toBeInTheDocument();
  });

  it("keyboard Enter on a control inside a widget is not taken as a drag", () => {
    useRunAnalytics.mockReturnValue({
      data: voiceAnalytics(),
      isPending: false,
      isError: false,
    });
    render(
      <RunAnalytics
        executionId="execution-1"
        envId="env-1"
        onOpenCalls={() => {}}
      />,
    );
    const button = screen.getByRole("button", { name: "Show Failed calls" });
    expect(fireEvent.keyDown(button, { key: "Enter", code: "Enter" })).toBe(
      true,
    );
  });
});
