import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  ThemeProvider,
  createTheme,
  getContrastRatio,
} from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import axios from "src/utils/axios";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import {
  CODE_AGENT_DETAIL,
  HOSTED_AGENT_DETAIL,
} from "src/api/agents/_fixtures/agentDetail";
import AgentDetailLayout from "../AgentDetailLayout";
import AgentDetailIndexRedirect from "../AgentDetailIndexRedirect";
import { agentDetailPath, resolveVersionId } from "../agentDetailTabs";
import { NOT_IN_RELEASE_SHORT } from "src/components/NotInRelease/notInRelease";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), put: vi.fn() },
  endpoints: {
    agentDefinitions: {
      detail: (id) => `/simulate/agent-definitions/${id}/`,
      versionDetail: (id, v) =>
        `/simulate/agent-definitions/${id}/versions/${v}/`,
      edit: (id) => `/simulate/agent-definitions/${id}/edit/`,
      versions: (id) => `/simulate/agent-definitions/${id}/versions/`,
    },
  },
}));

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

const Tab = ({ name }) => <div data-testid={`tab-${name}`} />;
Tab.propTypes = { name: PropTypes.string.isRequired };

// Mirrors the connected/:agentId block in routes/sections/dashboard.jsx.
// Pass a theme to render with the app's theme instead of MUI's default.
const renderAt = (url, theme) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const tree = (
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route
              path="/dashboard/agents/connected/:agentId"
              element={<AgentDetailLayout />}
            >
              <Route index element={<AgentDetailIndexRedirect />} />
              <Route path="overview" element={<Tab name="overview" />} />
              <Route path="definition" element={<Tab name="definition" />} />
              <Route path="versions" element={<Tab name="versions" />} />
              <Route
                path="environments"
                element={<Tab name="environments" />}
              />
            </Route>
            <Route path="*" element={<div data-testid="elsewhere" />} />
          </Routes>
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>
  );
  render(theme ? <ThemeProvider theme={theme}>{tree}</ThemeProvider> : tree);
  return client;
};

// The app's theme, built as theme/index.jsx builds it. Its Tabs default to
// textColor="inherit", which is what fades a disabled tab in the app.
const appTheme = (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
};

const rgb = (hex) =>
  `rgb(${hex
    .slice(1)
    .match(/../g)
    .map((v) => parseInt(v, 16))
    .join(", ")})`;

// The agent detail, its paginated versions list (newest first, `pageSize`
// per page) and each version's own detail.
const serve = (detail, { pageSize = 10 } = {}) => {
  const all = [...(detail.versions ?? [])].sort(
    (a, b) => b.version_number - a.version_number,
  );
  axios.get.mockImplementation((url, config) => {
    if (!url.includes(detail.id)) {
      return Promise.reject(new Error(`unexpected ${url}`));
    }
    if (url.endsWith("/versions/")) {
      const page = config.params.page;
      const results = all.slice((page - 1) * pageSize, page * pageSize);
      return Promise.resolve({
        data: {
          count: all.length,
          next: page * pageSize < all.length ? "next" : null,
          current_page: page,
          results,
        },
      });
    }
    const one = all.find((v) => url.endsWith(`/versions/${v.id}/`));
    if (one) return Promise.resolve({ data: one });
    return Promise.resolve({ data: detail });
  });
};

const HOSTED = HOSTED_AGENT_DETAIL;
const CODE = CODE_AGENT_DETAIL;
const base = (a) => `/dashboard/agents/connected/${a.id}`;

beforeEach(() => {
  axios.get.mockReset();
});

describe("agentDetailTabs", () => {
  it("takes ?v= as given, else the active version", () => {
    expect(resolveVersionId("v1", "v2")).toBe("v1");
    expect(resolveVersionId(null, "v2")).toBe("v2");
    expect(resolveVersionId(null, null)).toBeNull();
  });

  it("builds tab paths", () => {
    expect(agentDetailPath("a1")).toBe(
      "/dashboard/agents/connected/a1/overview",
    );
    expect(agentDetailPath("a1", "definition")).toBe(
      "/dashboard/agents/connected/a1/definition",
    );
  });
});

describe("AgentDetailLayout", () => {
  it("opens the bare agent URL on Overview", async () => {
    serve(HOSTED);
    renderAt(base(HOSTED));
    await screen.findByTestId("tab-overview");
    expect(currentLocation.pathname).toBe(`${base(HOSTED)}/overview`);
  });

  it("shows the hosted agent's header", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    expect(
      await screen.findByRole("heading", { name: "Northwind Pharmacy" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Vapi")).toBeInTheDocument();
    const subline = screen.getByTestId("agent-detail-subline");
    expect(subline).toHaveTextContent(HOSTED.assistant_id);
    expect(subline).toHaveTextContent("Created 13-10-2026");
    expect(subline).toHaveTextContent("No environments");
  });

  it("shows GitHub and the environment count for a code agent", async () => {
    serve(CODE);
    renderAt(`${base(CODE)}/overview`);
    const subline = await screen.findByTestId("agent-detail-subline");
    expect(subline).toHaveTextContent("GitHub");
    expect(subline).toHaveTextContent("1 environment");
  });

  it("keeps the header and tabs in place while the agent loads", async () => {
    axios.get.mockImplementation(() => new Promise(() => {}));
    renderAt(`${base(HOSTED)}/overview`);
    expect(
      await screen.findByTestId("agent-header-skeleton"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByTestId("tab-overview")).toBeInTheDocument();
  });

  it("goes back to Connected agents", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    fireEvent.click(await screen.findByRole("button", { name: "Back" }));
    expect(currentLocation.pathname).toBe("/dashboard/agents/connected");
  });

  it.each([
    ["hosted", HOSTED],
    ["code", CODE],
  ])("gives a %s agent both Versions and Environments", async (_, agent) => {
    serve(agent);
    renderAt(`${base(agent)}/overview`);
    await screen.findByRole("tab", { name: "Overview" });
    expect(screen.getByRole("tab", { name: /^Versions/ })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /^Environments/ }),
    ).toBeInTheDocument();
  });

  it("shows Constraints, Improvements and Connections as disabled tabs that say Not available in this release", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    await screen.findByRole("tab", { name: "Overview" });
    ["Constraints", "Improvements", "Connections"].forEach((name) => {
      const tab = screen.getByRole("tab", { name: new RegExp(name) });
      expect(tab).toBeDisabled();
      expect(
        within(tab).getByText("Not available in this release"),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/coming soon/i)).toBeNull();
  });

  // With the whole line in a chip on each of the
  // three tabs the strip was about 1090 px, so at 1024 px the Improvements
  // chip was cut mid-word and Connections sat behind a scroll arrow, and at
  // 1280 the Connections chip was cut to "No". The first round merged the
  // three into one tab, which hid two of them (owner rule: never hide a
  // visible feature to make a screen cleaner). Each keeps its own tab and
  // name; the chip carries a short word, and the whole line stays in its
  // tooltip and in what a screen reader reads.
  //
  // Widths are measured from the app's own font, Inter (@fontsource/inter
  // 5.2.8), by its advance widths with and without pair kerning, the wider:
  // tab labels at 12 px/500, the chip at 11 px/500. The built tabs are
  // taken at two-digit counts.
  const TAB_LABEL_PX = {
    Overview: 54.7,
    Definition: 55.2,
    "Versions 99": 68.9,
    "Environments 99": 97.3,
    Constraints: 66.1,
    Improvements: 82.7,
    Connections: 72.5,
  };
  const CHIP_TEXT_PX = { "N/A": 20.2 };
  // Theme MuiTab: 2 x 6 px padding, 8 px between tabs; the chip sits 6 px
  // after the name with 2 x 6 px padding of its own.
  const TAB_PADDING = 2 * 6;
  const TAB_GAP = 8;
  const CHIP_GAP = 6;
  const CHIP_PADDING = 2 * 6;
  // 1024 - 200 menu - 2 x 16 page padding.
  const STRIP_WIDTH_AT_1024 = 792;
  const UNRELEASED = ["Constraints", "Improvements", "Connections"];

  it("keeps Constraints, Improvements and Connections as three disabled tabs, each named, with a short chip, so the strip fits a 1024 px window", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    await screen.findByRole("tab", { name: "Overview" });
    const strip = screen.getByRole("tablist");
    // Overview, Definition, Versions, Environments, and the three unbuilt.
    expect(within(strip).getAllByRole("tab")).toHaveLength(7);
    expect(strip.querySelectorAll("[data-not-in-release]")).toHaveLength(3);

    const tabs = UNRELEASED.map((name) =>
      screen.getByRole("tab", { name: new RegExp(`^${name}`) }),
    );
    expect(new Set(tabs).size).toBe(3);
    tabs.forEach((tab, i) => {
      expect(tab).toBeDisabled();
      expect(within(tab).getByText(UNRELEASED[i])).toBeVisible();
      const chip = tab.querySelector("[data-not-in-release]");
      // The chip shows the short word; the whole line is still in the tab.
      expect(within(chip).getByText(NOT_IN_RELEASE_SHORT)).toBeVisible();
      expect(
        within(tab).getByText("Not available in this release"),
      ).toBeInTheDocument();
    });

    const chipPx = CHIP_TEXT_PX[NOT_IN_RELEASE_SHORT];
    expect(chipPx).toBeGreaterThan(0);
    const built = ["Overview", "Definition", "Versions 99", "Environments 99"]
      .map((label) => TAB_LABEL_PX[label] + TAB_PADDING)
      .reduce((a, b) => a + b, 0);
    const unbuilt = UNRELEASED.map(
      (label) =>
        TAB_LABEL_PX[label] + CHIP_GAP + CHIP_PADDING + chipPx + TAB_PADDING,
    ).reduce((a, b) => a + b, 0);
    expect(built + unbuilt + 6 * TAB_GAP).toBeLessThanOrEqual(
      STRIP_WIDTH_AT_1024,
    );
  });

  it("gives each short chip the whole line in a tooltip", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    await screen.findByRole("tab", { name: "Overview" });
    const tab = screen.getByRole("tab", { name: /^Improvements/ });
    const chip = tab.querySelector("[data-not-in-release]");
    // The tab is disabled and takes no pointer; the chip still does.
    expect(getComputedStyle(chip).pointerEvents).toBe("auto");
    fireEvent.mouseOver(chip);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Not available in this release",
    );
  });

  it.each(["light", "dark"])(
    "keeps the unreleased tabs and their chips readable in %s, at 4.5:1 or better, while they stay disabled",
    async (mode) => {
      const theme = appTheme(mode);
      const { secondary } = theme.palette.text;
      expect(
        getContrastRatio(secondary, theme.palette.background.paper),
      ).toBeGreaterThanOrEqual(4.5);
      serve(HOSTED);
      renderAt(`${base(HOSTED)}/overview`, theme);
      await screen.findByRole("tab", { name: "Overview" });
      ["Constraints", "Improvements", "Connections"].forEach((name) => {
        const tab = screen.getByRole("tab", { name: new RegExp(name) });
        // MUI fades a disabled tab to action.disabledOpacity (0.48), which
        // took the label and its chip to about 2.3:1.
        expect(getComputedStyle(tab).opacity).toBe("1");
        const chip = within(tab).getByText("Not available in this release");
        expect(getComputedStyle(chip).color).toBe(rgb(secondary));
        // Still disabled: no hover, no click.
        expect(tab).toBeDisabled();
        expect(getComputedStyle(tab).pointerEvents).toBe("none");
      });
    },
  );

  it("offers a scroll button when the tab strip is wider than the page, so no tab is cut off", async () => {
    // MUI shows the arrows when an IntersectionObserver reports the first or
    // last tab is not fully in view. setupTests stubs the observer as a no-op,
    // so report "not in view", as at narrower widths, for this test only.
    const SavedObserver = globalThis.IntersectionObserver;
    globalThis.IntersectionObserver = class {
      constructor(callback) {
        this.callback = callback;
      }

      observe() {
        this.callback([{ isIntersecting: false }]);
      }

      unobserve() {}

      disconnect() {}
    };
    try {
      serve(HOSTED);
      renderAt(`${base(HOSTED)}/overview`);
      await screen.findByRole("tab", { name: "Overview" });
      await waitFor(() =>
        expect(document.querySelector(".MuiTabs-scrollButtons")).not.toBeNull(),
      );
    } finally {
      globalThis.IntersectionObserver = SavedObserver;
    }
  });

  it("opens Environments on a hosted agent", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/environments`);
    await screen.findByTestId("tab-environments");
    expect(currentLocation.pathname).toBe(`${base(HOSTED)}/environments`);
  });

  it("counts versions from version_count, not the loaded list", async () => {
    serve({ ...HOSTED, version_count: 12 });
    renderAt(`${base(HOSTED)}/overview`);
    expect(
      await screen.findByRole("tab", { name: "Versions 12" }),
    ).toBeInTheDocument();
  });

  it("shows the version selector on the active version", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    const select = await screen.findByRole("combobox");
    await waitFor(() => expect(select).toHaveTextContent("v2 · active"));
  });

  it("writes the picked version to ?v= and clears it for the active one", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    fireEvent.mouseDown(await screen.findByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: /^v1/ }));
    await waitFor(() => expect(currentLocation.search).toBe("?v=v1-id"));
    expect(screen.getByRole("combobox")).toHaveTextContent("Viewing v1");

    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: /^v2/ }));
    await waitFor(() => expect(currentLocation.search).toBe(""));
  });

  it("lists versions from the paginated versions list", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    fireEvent.mouseDown(await screen.findByRole("combobox"));
    await screen.findByRole("option", { name: /^v1/ });
    expect(axios.get).toHaveBeenCalledWith(
      `/simulate/agent-definitions/${HOSTED.id}/versions/`,
      { params: { page: 1, limit: 10 } },
    );
  });

  it("pins a picked version that isn't on the first page to the top of the menu", async () => {
    serve(HOSTED, { pageSize: 1 });
    renderAt(`${base(HOSTED)}/overview?v=v1-id`);
    const select = await screen.findByRole("combobox");
    await waitFor(() => expect(select).toHaveTextContent("Viewing v1"));
    fireEvent.mouseDown(screen.getByRole("combobox"));
    await waitFor(() =>
      expect(
        screen.getAllByRole("option").map((o) => o.textContent.slice(0, 2)),
      ).toEqual(["v1", "v2"]),
    );
  });

  it("keeps the picked version when switching tabs", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview?v=v1-id`);
    fireEvent.click(await screen.findByRole("tab", { name: "Definition" }));
    await screen.findByTestId("tab-definition");
    expect(currentLocation.search).toBe("?v=v1-id");
  });

  it("switches tabs by path", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    fireEvent.click(await screen.findByRole("tab", { name: "Definition" }));
    await screen.findByTestId("tab-definition");
    expect(currentLocation.pathname).toBe(`${base(HOSTED)}/definition`);
  });

  it("keeps the page when a background refetch fails", async () => {
    serve(HOSTED);
    const client = renderAt(`${base(HOSTED)}/overview`);
    await screen.findByRole("heading", { name: "Northwind Pharmacy" });
    axios.get.mockRejectedValue({ message: "Server error" });
    await act(async () => {
      await client.refetchQueries();
    });
    await waitFor(() =>
      expect(client.getQueryState(["agents", "detail", HOSTED.id]).status).toBe(
        "error",
      ),
    );
    expect(
      screen.getByRole("heading", { name: "Northwind Pharmacy" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Server error")).toBeNull();
  });

  it("shows the server's message when the agent can't be loaded", async () => {
    axios.get.mockRejectedValue({ message: "Agent definition not found" });
    renderAt(`${base(HOSTED)}/overview`);
    expect(
      await screen.findByText("Agent definition not found"),
    ).toBeInTheDocument();
  });
});
