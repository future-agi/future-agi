import { beforeEach, describe, expect, it, vi } from "vitest";
import { createTheme } from "@mui/material/styles";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import DashboardDetailView from "../DashboardDetailView";

// On a dashboard the breadcrumb, the title, the date row and the widget
// cards share one left edge. The header rows keep the 24px inset because the
// widgets grid below needs it (its rows reach 28px left for the add-to-row
// "+" button); the title's hover frame sits in the gutter, so its text starts
// on that edge. The "+ Add description..." placeholder reads at a contrast
// of at least 4.5:1 in light and dark theme.
//
// The checks read only properties the view sets on the element itself
// (padding, margin, colour, opacity): no widths, no getBoundingClientRect and
// no inherited styles. The app theme uses MUI's default 8px spacing and the
// shared test theme uses 0.25rem, so every render here passes its own theme.
// The title and description frames sit on a negative margin, which jsdom's
// computed style cannot hold, so those are read from the element's own rule
// (frameOf below).

const h = vi.hoisted(() => ({
  widgets: [],
  // Catch-all for every other dashboards hook the view calls (update,
  // delete, reorder, duplicate, ...): render has no QueryClientProvider.
  mutation: { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false },
}));

vi.mock("src/hooks/useDashboards", async (importOriginal) => {
  const actual = await importOriginal();
  const hookStubs = Object.fromEntries(
    Object.keys(actual)
      .filter((name) => name.startsWith("use"))
      .map((name) => [name, () => h.mutation]),
  );
  return {
    ...actual,
    ...hookStubs,
    useDashboardDetail: () => ({
      data: {
        id: "dash-1",
        name: "Untitled",
        description: "",
        widgets: h.widgets,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    }),
  };
});

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ dashboardId: "dash-1" }),
  useNavigate: () => vi.fn(),
}));

vi.mock("../hooks/useCanEditDashboard", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  }),
}));

// The header is under test, not the charts: stub the chart library outright
// (as the widget editor tests do) and the widget chart itself.
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("../WidgetChart", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <div data-testid="widget-chart" />,
}));

vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

const themeFor = (mode) => createTheme({ palette: palette(mode) });

const renderView = (mode = "light") =>
  render(<DashboardDetailView />, { theme: themeFor(mode) });

// WCAG contrast ratio of two colours given as #rrggbb or rgb(...). An unset
// colour counts as no contrast.
const rgbOf = (color) => {
  const hex = /^#([0-9a-f]{6})$/i.exec(color ?? "");
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16));
  const rgb = /^rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(color ?? "");
  return rgb ? rgb.slice(1, 4).map(Number) : null;
};
const luminance = ([r, g, b]) => {
  const channel = (value) => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
};
const contrast = (foreground, background) => {
  const fg = rgbOf(foreground);
  const bg = rgbOf(background);
  if (!fg || !bg) return 0;
  const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
};

// jsdom drops a negative margin: its CSSStyleDeclaration (cssstyle 4.4) takes
// only margins of 0 or more, so margin-left: -10px never reaches
// getComputedStyle. toHaveStyle({ marginLeft: "-10px" }) checks nothing
// either: it builds the expected value through the same cssstyle, gets "",
// and then compares getPropertyValue("marginLeft"), which is "" on every
// element. So read the value the element's own emotion rule declares (theme
// styles and sx share one class); undefined when the rule declares none.
function declaredOnOwnRule(element, property) {
  return Array.from(document.styleSheets)
    .flatMap((sheet) => Array.from(sheet.cssRules))
    .filter((rule) =>
      Array.from(element.classList).some(
        (name) => rule.selectorText === `.${name}`,
      ),
    )
    .map((rule) => rule.style.getPropertyValue(property))
    .filter(Boolean)
    .pop();
}

// Where an inline-edit field's 2px frame sits: the -10px margin moves it out
// into the gutter, with 8px of padding inside it. A matching padding also
// shows the element's own rule was found.
const frameOf = (element) => ({
  marginLeft: declaredOnOwnRule(element, "margin-left"),
  paddingLeft: declaredOnOwnRule(element, "padding-left"),
});
const FRAME_ON_GUTTER = { marginLeft: "-10px", paddingLeft: "8px" };

const PLACEHOLDER = "+ Add description...";

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/dashboard/dashboards/dash-1");
  h.widgets = [];
});

describe("Dashboard view header on the page gutter", () => {
  it("starts the breadcrumb row on the 24px inset the widget cards use", () => {
    renderView();

    const topBar = screen.getByText("Dashboards").closest("nav").parentElement;
    expect(topBar).toHaveStyle({ paddingLeft: "24px", paddingRight: "24px" });
  });

  it("starts the title text on the same edge as the breadcrumb", () => {
    renderView();

    const title = screen.getByRole("heading", { name: "Untitled" });
    expect(title.parentElement).toHaveStyle({ paddingLeft: "24px" });
    // 24px inset, then -10px of margin, the 2px frame and 8px of padding:
    // the text starts on the breadcrumb's edge.
    expect(frameOf(title)).toEqual(FRAME_ON_GUTTER);
  });

  it("keeps the description in line with the title", () => {
    renderView();

    const field = screen.getByText(PLACEHOLDER).parentElement;
    expect(frameOf(field)).toEqual(FRAME_ON_GUTTER);
  });

  it("keeps the description text in place while it is being edited", () => {
    renderView();

    fireEvent.click(screen.getByText(PLACEHOLDER));
    const field = screen
      .getByPlaceholderText(PLACEHOLDER)
      .closest(".MuiInputBase-root");
    expect(frameOf(field)).toEqual(FRAME_ON_GUTTER);
  });

  it("starts the date filter row on the widget cards' 24px edge when the dashboard has widgets", () => {
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
    renderView();

    const dateRow = screen
      .getByText("7D")
      .closest(".MuiChip-root").parentElement;
    expect(dateRow).toHaveStyle({ paddingLeft: "24px" });
  });

  it("keeps the widgets grid at 24px so the add-to-row button stays on the page", () => {
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
    renderView();

    // The row reaches 28px to the left of the grid for its "+" button.
    const row = screen.getByRole("button", {
      name: "Add widget to row",
    }).parentElement;
    expect(row).toHaveStyle({ marginLeft: "-28px", paddingLeft: "28px" });
    expect(row.parentElement).toHaveStyle({ paddingLeft: "24px" });
  });
});

describe("Dashboard description placeholder contrast", () => {
  it.each(["light", "dark"])(
    "shows the placeholder at readable contrast in %s theme",
    (mode) => {
      renderView(mode);

      const placeholder = window.getComputedStyle(
        screen.getByText(PLACEHOLDER),
      );
      // No opacity on top of the colour: the colour alone sets the contrast.
      expect(["", "1"]).toContain(placeholder.opacity);
      expect(
        contrast(placeholder.color, themeFor(mode).palette.background.paper),
      ).toBeGreaterThanOrEqual(4.5);
    },
  );

  it("shows the text typed into an empty description at readable contrast in dark theme", () => {
    renderView("dark");

    fireEvent.click(screen.getByText(PLACEHOLDER));
    const field = screen
      .getByPlaceholderText(PLACEHOLDER)
      .closest(".MuiInputBase-root");
    expect(
      contrast(
        window.getComputedStyle(field).color,
        themeFor("dark").palette.background.paper,
      ),
    ).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps the breadcrumb, the title and the placeholder text", () => {
    renderView("dark");

    expect(screen.getByText("Dashboards")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Untitled" }),
    ).toBeInTheDocument();
    expect(screen.getByText(PLACEHOLDER)).toBeInTheDocument();
  });
});
