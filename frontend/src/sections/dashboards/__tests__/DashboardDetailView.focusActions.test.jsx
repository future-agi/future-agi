import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import DashboardDetailView from "../DashboardDetailView";

// W10-DASHBOARDS-U07: a widget card's Edit / options buttons and a row's "+"
// sit at opacity 0 until hover, so Tab landed on buttons nobody could see.
// Harness mirrors DashboardDetailView.test.jsx.
const h = vi.hoisted(() => ({
  widgets: [{ id: "w-1", name: "Tokens", position: 0, width: 12 }],
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({
    data: { id: "dash-1", name: "My Dash", widgets: h.widgets },
    isLoading: false,
  }),
  useUpdateDashboard: () => ({ mutate: vi.fn() }),
  useUpdateWidget: () => ({ mutate: vi.fn() }),
  useDeleteWidget: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteDashboard: () => ({ mutate: vi.fn(), isPending: false }),
  useReorderWidgets: () => ({ mutate: vi.fn() }),
  useDuplicateWidget: () => ({ mutate: vi.fn() }),
  useCreateWidget: () => ({ mutate: vi.fn() }),
}));

vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
  useParams: () => ({ dashboardId: "dash-1" }),
  useNavigate: () => vi.fn(),
}));

vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => ({
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  }),
}));

vi.mock("../WidgetChart", () => ({
  default: () => <div data-testid="widget-chart" />,
}));

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

// jsdom cannot paint opacity and only matches :focus-within on the focused
// element itself, so read the emitted rule instead (as the sandbox
// KeyboardFocus test does): the opacity a container sets for its hidden
// `.${hiddenClass}` controls while it is in `state`.
function revealOpacity(control, hiddenClass, state) {
  const selectors = [];
  for (let node = control; node; node = node.parentElement) {
    for (const name of node.classList) {
      selectors.push(`.${name}${state} .${hiddenClass}`);
    }
  }
  const rule = Array.from(document.styleSheets)
    .flatMap((sheet) => Array.from(sheet.cssRules))
    .find((candidate) =>
      candidate.selectorText
        ?.split(",")
        .some((part) => selectors.includes(part.trim().replace(/\s+/g, " "))),
    );
  return rule?.style.getPropertyValue("opacity");
}

describe("DashboardDetailView — hover actions under keyboard focus (W10-DASHBOARDS-U07)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/dashboard/dashboards/dash-1");
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
  });

  it("shows a widget's Edit and options buttons when focus is inside the card", () => {
    const { container } = render(<DashboardDetailView />);
    const card = container.querySelector('[data-widget-id="w-1"]');
    for (const name of ["Edit", "Widget options"]) {
      const button = within(card).getByRole("button", { name });
      // Pointer users see the actions on hover...
      expect(revealOpacity(button, "widget-actions", ":hover")).toBe("1");
      // ...and Tab must not land on an invisible button.
      expect(revealOpacity(button, "widget-actions", ":focus-within")).toBe(
        "1",
      );
    }
  });

  it("shows the add-to-row button when focus is inside the row", () => {
    render(<DashboardDetailView />);
    const add = screen.getByRole("button", { name: "Add widget to row" });
    expect(revealOpacity(add, "row-add-btn", ":hover")).toBe("1");
    expect(revealOpacity(add, "row-add-btn", ":focus-within")).toBe("1");
  });
});
