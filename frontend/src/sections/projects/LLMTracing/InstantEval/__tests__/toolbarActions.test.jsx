import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ObserveToolbar from "../../ObserveToolbar";

vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("../../TraceFilterPanel", () => ({ default: () => null }));
vi.mock("../../DisplayPanel", () => ({ default: () => null }));
vi.mock("../../BulkActionsBar", () => ({ default: () => null }));
vi.mock("../../tabStore", () => ({
  useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const renderToolbar = (props) =>
  render(
    <ObserveToolbar
      inline
      isFilterOpen={false}
      onFilterToggle={vi.fn()}
      onApplyExtraFilters={vi.fn()}
      {...props}
    />,
  );

const handlers = () => ({
  onInstantEval: vi.fn(),
  onInsights: vi.fn(),
  onAddEvals: vi.fn(),
});

const openMenu = () =>
  fireEvent.click(screen.getByRole("button", { name: "Evaluate" }));

const menuTitles = () =>
  screen
    .getAllByRole("menuitem")
    .map((item) => item.querySelector("p, span")?.textContent);

describe("ObserveToolbar Evaluate menu", () => {
  it.each([
    ["traces", "trace", "traces"],
    ["sessions", "session", "sessions"],
    ["users", "user", "users"],
  ])("offers every action on the %s view", (mode, noun, plural) => {
    const props = handlers();
    renderToolbar({ mode, ...props });
    // The three old buttons are one menu now.
    expect(screen.queryByRole("button", { name: "Instant eval" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add Evals" })).toBeNull();
    openMenu();
    expect(menuTitles()).toEqual([
      "Ask a question",
      "Find patterns",
      "Add evaluators",
    ]);
    expect(
      screen.getByText(`Jev answers it for every ${noun} in view, in seconds.`),
    ).toBeInTheDocument();
    expect(
      screen.getByText(`Start an Insights report on these ${plural}.`),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("menuitem", { name: /Ask a question/ }));
    expect(props.onInstantEval).toHaveBeenCalledTimes(1);
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Find patterns/ }));
    expect(props.onInsights).toHaveBeenCalledTimes(1);
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Add evaluators/ }));
    expect(props.onAddEvals).toHaveBeenCalledTimes(1);
  });

  it("asks about spans on the Spans tab, without Find patterns", () => {
    const props = handlers();
    renderToolbar({ mode: "traces", isSpansView: true, ...props });
    openMenu();
    expect(menuTitles()).toEqual(["Ask a question", "Add evaluators"]);
    expect(
      screen.getByText("Jev answers it for every span in view, in seconds."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /Ask a question/ }));
    expect(props.onInstantEval).toHaveBeenCalledTimes(1);
  });

  it("keeps only Add evaluators in compare mode", () => {
    renderToolbar({ mode: "sessions", isCompareActive: true, ...handlers() });
    openMenu();
    expect(menuTitles()).toEqual(["Add evaluators"]);
  });

  it("renders nothing without a project's handlers", () => {
    renderToolbar({ mode: "users" });
    expect(screen.queryByRole("button", { name: "Evaluate" })).toBeNull();
  });
});
