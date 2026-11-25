import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import ObserveToolbar from "../../ObserveToolbar";

// Features the deployment's license or plan denies.
const capabilities = vi.hoisted(() => ({ denied: new Set() }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureDenied: (feature) => capabilities.denied.has(feature),
}));

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
  beforeEach(() => {
    capabilities.denied = new Set();
  });

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
      screen.getByText(
        `Jev answers it for a sample or every ${noun} in view; the first answers come in seconds.`,
      ),
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
      screen.getByText(
        "Jev answers it for a sample or every span in view; the first answers come in seconds.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /Ask a question/ }));
    expect(props.onInstantEval).toHaveBeenCalledTimes(1);
  });

  // Instant eval and Insights are Enterprise (e.g. locked on open source):
  // marked, and still clickable, onto the gate their pages show.
  it("marks the Enterprise actions and still opens them", () => {
    capabilities.denied = new Set(["instant_eval", "insights"]);
    const props = handlers();
    renderToolbar({ mode: "sessions", ...props });
    openMenu();
    const item = (name) => screen.getByRole("menuitem", { name });
    expect(
      within(item(/Ask a question/)).getByText("Enterprise"),
    ).toBeInTheDocument();
    expect(
      within(item(/Find patterns/)).getByText("Enterprise"),
    ).toBeInTheDocument();
    expect(within(item(/Add evaluators/)).queryByText("Enterprise")).toBeNull();

    fireEvent.click(item(/Ask a question/));
    expect(props.onInstantEval).toHaveBeenCalledTimes(1);
    openMenu();
    fireEvent.click(item(/Find patterns/));
    expect(props.onInsights).toHaveBeenCalledTimes(1);
  });

  it("marks nothing when the features are allowed", () => {
    renderToolbar({ mode: "traces", ...handlers() });
    openMenu();
    expect(screen.queryByText("Enterprise")).toBeNull();
  });

  it("keeps only Add evaluators in compare mode", () => {
    renderToolbar({ mode: "sessions", isCompareActive: true, ...handlers() });
    openMenu();
    expect(menuTitles()).toEqual(["Add evaluators"]);
  });

  it("keeps only Add evaluators on a simulator (voice) project", () => {
    // Ask and Find patterns read the trace list, not the calls on screen.
    renderToolbar({ mode: "traces", isSimulator: true, ...handlers() });
    openMenu();
    expect(menuTitles()).toEqual(["Add evaluators"]);
  });

  it("rings the Evaluate button when it has keyboard focus", () => {
    renderToolbar({ mode: "sessions", ...handlers() });
    const button = screen.getByRole("button", { name: "Evaluate" });
    // jsdom can't match :focus-visible, so read the rule emotion wrote for
    // the button's own class.
    const rules = Array.from(document.styleSheets).flatMap((sheet) =>
      Array.from(sheet.cssRules, (rule) => rule.cssText),
    );
    const ring = rules.find((rule) =>
      Array.from(button.classList).some((name) =>
        rule.startsWith(`.${name}:focus-visible`),
      ),
    );
    expect(ring).toMatch(/outline: 2px solid/);
  });

  it("renders nothing without a project's handlers", () => {
    renderToolbar({ mode: "users" });
    expect(screen.queryByRole("button", { name: "Evaluate" })).toBeNull();
  });
});
