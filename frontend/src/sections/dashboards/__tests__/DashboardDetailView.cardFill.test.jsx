import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import Section from "src/components/section";
import DashboardDetailView from "../DashboardDetailView";
import { DEFAULT_WIDGET_HEIGHT, MIN_WIDGET_HEIGHT } from "../constants";

// A widget card is a fixed-height Section panel. WidgetChart sizes the chart
// from a ResizeObserver on a height:100% box, so every box between the card
// and that box must take its height from the card. When one of them is a
// plain block, the chart measures itself, grows on every render and the card
// clips it to its top tick. jsdom has no layout, so these pin the styles that
// make each box fill, not pixel sizes. Harness mirrors
// DashboardDetailView.test.jsx.
const h = vi.hoisted(() => ({
  widgets: [{ id: "w-1", name: "Tokens", position: 0, width: 12 }],
  updateWidget: { mutate: vi.fn() },
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({
    data: { id: "dash-1", name: "My Dash", widgets: h.widgets },
    isLoading: false,
  }),
  useUpdateDashboard: () => ({ mutate: vi.fn() }),
  useUpdateWidget: () => h.updateWidget,
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

describe("DashboardDetailView — a widget card gives its chart the card's height", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
  });

  it("makes every box from the card down to the chart a flex fill", () => {
    const { container } = render(<DashboardDetailView />);
    const card = container.querySelector('[data-widget-id="w-1"] > div');
    const chartBox = screen.getByTestId("widget-chart").parentElement;
    expect(card).toContainElement(chartBox);

    // The card is the fixed-height column the room comes from.
    expect(card).toHaveStyle({
      height: `${DEFAULT_WIDGET_HEIGHT}px`,
      display: "flex",
      flexDirection: "column",
    });

    // From the card's body down to the chart's own box, each box grows into
    // the room it is given and may be shorter than its content...
    const between = [];
    for (let node = chartBox; node !== card; node = node.parentElement) {
      between.unshift(node);
    }
    for (const node of between) {
      expect(node).toHaveStyle({ flexGrow: "1", minHeight: "0" });
    }
    // ...and each box that holds the next one is a column, so the next box's
    // flex height is measured against it.
    for (const node of between.slice(0, -1)) {
      expect(node).toHaveStyle({ display: "flex", flexDirection: "column" });
    }
  });

  it("drags a row's height onto its card and saves it from the card's own height", () => {
    const { container } = render(<DashboardDetailView />);
    const card = container.querySelector('[data-widget-id="w-1"] > div');
    const handle = container.querySelector(".row-resize-bar").parentElement;
    // jsdom has no layout, so start from whatever height the card reports.
    const expected = Math.max(MIN_WIDGET_HEIGHT, card.offsetHeight + 200);

    fireEvent.mouseDown(handle, { clientY: 100 });
    fireEvent.mouseMove(document, { clientY: 300 });
    fireEvent.mouseUp(document, { clientY: 300 });

    // The drag previews the new height on the card itself...
    expect(card.style.height).toBe(`${expected}px`);
    // ...and saves the height the card was dragged to.
    expect(h.updateWidget.mutate).toHaveBeenCalledWith({
      dashboardId: "dash-1",
      widgetId: "w-1",
      data: { height: expected },
    });
  });
});

describe("Section — a panel's body fills the panel only when asked", () => {
  const panel = (props) => (
    <Section variant="panel" {...props}>
      <div data-testid="content" />
    </Section>
  );
  const body = () => screen.getByTestId("content").parentElement;

  it("keeps a panel's body a plain block by default", () => {
    render(panel());
    expect(getComputedStyle(body()).display).not.toBe("flex");
    expect(getComputedStyle(body()).flexGrow).not.toBe("1");
  });

  it("makes the body a flex column fill with fillBody and keeps its padding", () => {
    const view = render(panel());
    const padding = getComputedStyle(body()).paddingTop;
    expect(padding).not.toBe("");

    view.rerender(panel({ fillBody: true }));
    expect(body()).toHaveStyle({
      flexGrow: "1",
      minHeight: "0",
      display: "flex",
      flexDirection: "column",
    });
    expect(getComputedStyle(body()).paddingTop).toBe(padding);
  });
});
