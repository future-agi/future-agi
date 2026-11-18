import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import ReactApexChart from "react-apexcharts";
import { dsPalette, dsTypography } from "src/theme/ds-tokens";
import { useSkillUsage } from "src/api/skills";
import UsageTab, { channelLabel } from "./UsageTab";

vi.mock("src/api/skills", () => ({ useSkillUsage: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("react-apexcharts", () => ({ default: vi.fn(() => null) }));
const day = new Date().toISOString().slice(0, 10);
const usage = {
  total: 9,
  lastFetchedAt: `${day}T10:00:00Z`,
  byDay: [
    { day, version: 1, count: 4 },
    { day, version: 3, count: 5 },
  ],
  byVersion: [
    { version: 1, count: 4 },
    { version: 3, count: 5 },
  ],
  consumers: [
    {
      label: "Test integration",
      channel: "api",
      count: 9,
      lastAt: `${day}T10:00:00Z`,
    },
  ],
};
const skill = { id: "skill-a", latestVersion: 3 };
const success = (data) => ({
  data,
  isPending: false,
  isError: false,
  refetch: vi.fn(),
});
function show(search = "", mode = "light") {
  const theme = createTheme({
    palette: dsPalette(mode),
    typography: dsTypography,
  });
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[`/${search}`]}>
        <UsageTab skill={skill} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  useSkillUsage.mockReturnValue(success(usage));
});
afterEach(cleanup);

describe("Usage tab", () => {
  it.each(["light", "dark"])(
    "uses the %s data palette and stacked daily series",
    (mode) => {
      show("", mode);
      fireEvent.click(screen.getByRole("button", { name: "Counts and details" }));
      fireEvent.click(screen.getByRole("button", { name: "About this data" }));
      const props = ReactApexChart.mock.calls.at(-1)[0];
      expect(props.type).toBe("bar");
      expect(props.options.chart.stacked).toBe(true);
      expect(props.options.colors).toEqual([
        dsPalette(mode).data[0],
        dsPalette(mode).data[2],
      ]);
      expect(props.options.stroke.colors).toEqual([
        dsPalette(mode).background.paper,
      ]);
      expect(props.options.legend.show).toBe(true);
      expect(props.options.tooltip.theme).toBe(mode);
      expect(props.series.map((item) => item.name)).toEqual(["v1", "v3"]);
      expect(props.options.xaxis.type).toBe("datetime");
      expect(props.series[0].data.at(-1)).toEqual({
        x: Date.parse(`${day}T00:00:00Z`),
        y: 4,
      });
      expect(screen.getByText("Versions fetched")).toBeInTheDocument();
      expect(
        screen.getByText(
          "Counts requests that fetched this skill. A fetch doesn't prove an agent used it.",
        ),
      ).toBeInTheDocument();
    },
  );
  it("queries every supported time range", async () => {
    show();
    expect(useSkillUsage).toHaveBeenLastCalledWith("skill-a", 30, "skill");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Time range" }));
    fireEvent.click(await screen.findByRole("option", { name: "Last 7 days" }));
    expect(useSkillUsage).toHaveBeenLastCalledWith("skill-a", 7, "skill");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Time range" }));
    fireEvent.click(
      await screen.findByRole("option", { name: "Last 90 days" }),
    );
    expect(useSkillUsage).toHaveBeenLastCalledWith("skill-a", 90, "skill");
  });
  it("provides exact daily counts and a consumer table without relying on chart colors", () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Counts and details" }));
    fireEvent.click(screen.getByRole("tab", { name: "Table", exact: true }));
    const daily = within(
      screen.getByRole("table", { name: "Daily fetches by version" }),
    );
    expect(daily.getByRole("cell", { name: "v1" })).toBeInTheDocument();
    expect(daily.getByRole("cell", { name: "4" })).toBeInTheDocument();
    expect(daily.getByRole("cell", { name: "v3" })).toBeInTheDocument();
    const consumers = within(
      screen.getByRole("table", { name: "Skill consumers" }),
    );
    expect(
      consumers.getByRole("cell", { name: "Test integration" }),
    ).toBeInTheDocument();
    expect(consumers.getByRole("cell", { name: "API" })).toBeInTheDocument();
    expect(consumers.getByRole("cell", { name: "9" })).toBeInTheDocument();
  });
  it("renders zero usage honestly and keeps the fetch caveat visible", () => {
    useSkillUsage.mockReturnValue(
      success({ ...usage, total: 0, byDay: [], byVersion: [], consumers: [] }),
    );
    show();
    expect(screen.getByText("No fetches in this period")).toBeInTheDocument();
    expect(screen.getByText("Fetches appear when someone resolves, downloads or reads this skill through the API, the SDK or MCP.")).toBeInTheDocument();
    expect(screen.queryByText(/Uses appear/)).not.toBeInTheDocument();
    expect(screen.getByText("Retrieval (fetches)")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "About this data" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "About this data" }));
    expect(screen.getByText(/A fetch doesn't prove/)).toBeInTheDocument();
    expect(ReactApexChart).not.toHaveBeenCalled();
  });
  it.each(["loading", "error", "empty"])(
    "supports the %s URL preview",
    (state) => {
      show(`?state=${state}`);
      if (state === "loading")
        expect(screen.getByLabelText("Loading usage")).toBeInTheDocument();
      if (state === "error")
        expect(screen.getByText("Could not load usage")).toBeInTheDocument();
      if (state === "empty")
        expect(
          screen.getByText("No fetches in this period"),
        ).toBeInTheDocument();
      expect(ReactApexChart).not.toHaveBeenCalled();
    },
  );
  it("titles the tab's section Retrieval (fetches) in every state, with the time range beside it", () => {
    for (const state of ["", "?state=loading", "?state=error", "?state=empty"]) {
      const view = show(state);
      expect(screen.getByText("Retrieval (fetches)")).toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: "Time range" })).toBeInTheDocument();
      expect(screen.queryByText(/^Used\b|Uses appear|in use/)).not.toBeInTheDocument();
      view.unmount();
    }
  });
  it("labels SDK fetches as SDK with the SDK version that fetched, and other channels by name", () => {
    useSkillUsage.mockReturnValue(success({ ...usage, consumers: [
      { label: "refund-agent key", channel: "sdk", sdkVersion: "futureagi-python 1.4.0", count: 4, lastAt: `${day}T10:00:00Z` },
      { label: "ci key", channel: "sdk", count: 2, lastAt: `${day}T10:00:00Z` },
      { label: "Docs bot", channel: "mcp", count: 2, lastAt: `${day}T10:00:00Z` },
      { label: "Release script", channel: "download", count: 1, lastAt: `${day}T10:00:00Z` },
    ] }));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Counts and details" }));
    const consumers = within(screen.getByRole("table", { name: "Skill consumers" }));
    expect(consumers.getByRole("cell", { name: "SDK · futureagi-python 1.4.0" })).toBeInTheDocument();
    expect(consumers.getByRole("cell", { name: "SDK" })).toBeInTheDocument();
    expect(consumers.getByRole("cell", { name: "MCP" })).toBeInTheDocument();
    expect(consumers.getByRole("cell", { name: "Download" })).toBeInTheDocument();
    expect(consumers.queryByRole("cell", { name: "sdk" })).not.toBeInTheDocument();
  });
  it.each([
    [{ channel: "sdk", sdkVersion: " futureagi-typescript 2.0.1 " }, "SDK · futureagi-typescript 2.0.1"],
    [{ channel: "sdk", sdk_version: "futureagi-python 1.4.0" }, "SDK · futureagi-python 1.4.0"],
    [{ channel: "sdk", sdkVersion: null }, "SDK"],
    [{ channel: "api", sdkVersion: "futureagi-python 1.4.0" }, "API"],
    [{ channel: "webhook" }, "webhook"],
    [{}, "Unknown"],
  ])("names the channel of %j as %s", (item, label) => {
    expect(channelLabel(item)).toBe(label);
  });
  it("retries actual API failures", () => {
    const refetch = vi.fn();
    useSkillUsage.mockReturnValue({ isError: true, refetch });
    show();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});

vi.mock("src/sections/skills/governance/AdoptionPanel", async () => {
  const { default: TechnicalDetails } = await import("../components/TechnicalDetails");
  return { default: ({ summary, about, details, aboutOpen, onAboutToggle }) => <div>Adoption panel{summary}<TechnicalDetails title="Counts and details">{details}</TechnicalDetails><TechnicalDetails title="About this data" open={aboutOpen} onToggle={onAboutToggle}>{about}</TechnicalDetails></div> };
});

it('shows one time control and keeps detailed counts disclosed by default', () => {
  show();
  expect(screen.getAllByRole('combobox')).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Counts and details' })).toHaveAttribute('aria-expanded', 'false');
  // The explanatory text lives in one disclosure, never several.
  expect(screen.getAllByRole('button', { name: 'About this data' })).toHaveLength(1);
  expect(ReactApexChart).not.toHaveBeenCalled();
});

it("keeps About this data open when the usage it describes finishes loading", () => {
  useSkillUsage.mockReturnValue({ data: undefined, isPending: true, isError: false, refetch: vi.fn() });
  const theme = createTheme({ palette: dsPalette("light"), typography: dsTypography });
  const tree = () => (
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={["/"]}>
        <UsageTab skill={skill} />
      </MemoryRouter>
    </ThemeProvider>
  );
  const view = render(tree());
  fireEvent.click(screen.getByRole("button", { name: "About this data" }));
  expect(screen.getByText(/A fetch doesn.t prove an agent used it\./)).toBeVisible();
  useSkillUsage.mockReturnValue(success(usage));
  view.rerender(tree());
  expect(screen.getByRole("button", { name: "About this data" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText(/A fetch doesn.t prove an agent used it\./)).toBeVisible();
});

// SKILL-TRACES §7.4 "Activity tab" and §16 UXF-07 (UXF5 commit 2): the context Activity tab is
// this component with kind "agent_context". It reads the shared usage route and has no
// label-move fetch adoption.
describe("agent context Activity", () => {
  const context = { id: "context-a", name: "support-agent", latestVersion: 3 };
  const showContext = () => render(
    <ThemeProvider theme={createTheme({ palette: dsPalette("light"), typography: dsTypography })}>
      <MemoryRouter initialEntries={["/"]}>
        <UsageTab kind="agent_context" skill={context} />
      </MemoryRouter>
    </ThemeProvider>,
  );

  it("shows the context's fetches without the skill adoption panel", () => {
    useSkillUsage.mockReturnValue(success({ ...usage, consumers: [{ ...usage.consumers[0], channel: "sdk", sdkVersion: "1.4.0" }] }));
    showContext();
    // The shared usage route, named with the context kind: never a skill read of a context id.
    expect(useSkillUsage).toHaveBeenLastCalledWith("context-a", 30, "agent_context");
    expect(screen.getByText("Retrieval (fetches)")).toBeInTheDocument();
    expect(screen.queryByText("Adoption panel")).not.toBeInTheDocument();
    const consumers = within(screen.getByRole("table", { name: "Agent context consumers" }));
    expect(consumers.getByText("SDK · 1.4.0")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "About this data" }));
    expect(screen.getByText("Counts requests that fetched this agent context. A fetch doesn't prove an agent used it.")).toBeVisible();
    expect(screen.queryByText(/fetched this skill/)).not.toBeInTheDocument();
  });

  it("names the context in the empty and error states", () => {
    useSkillUsage.mockReturnValue(success({ ...usage, total: 0 }));
    const view = showContext();
    expect(screen.getByText("Fetches appear when someone resolves, downloads or reads this agent context through the API, the SDK or MCP.")).toBeInTheDocument();
    view.unmount();
    useSkillUsage.mockReturnValue({ isError: true, refetch: vi.fn() });
    showContext();
    expect(screen.getByText("Your agent context and versions are unchanged.")).toBeInTheDocument();
  });
});

// UXF-07: the context Activity tab's first-day, empty and error states.
describe("agent context Activity states", () => {
  const context = { id: "context-a", name: "support-agent", latestVersion: 3 };
  const showContext = (props = {}) => render(
    <ThemeProvider theme={createTheme({ palette: dsPalette("light"), typography: dsTypography })}>
      <MemoryRouter initialEntries={["/"]}>
        <UsageTab kind="agent_context" skill={context} {...props} />
      </MemoryRouter>
    </ThemeProvider>,
  );
  const none = { ...usage, total: 0, byDay: [], byVersion: [], consumers: [], lastFetchedAt: null };

  it("says nobody has fetched it yet on the first day, with Install as the one action", () => {
    const onInstall = vi.fn();
    useSkillUsage.mockReturnValue(success({ ...none, firstFetchedOn: null }));
    showContext({ onInstall });
    expect(screen.getByText("Nobody has fetched this agent context yet")).toBeInTheDocument();
    expect(screen.queryByText("No fetches in this period")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    expect(onInstall).toHaveBeenCalledTimes(1);
    expect(ReactApexChart).not.toHaveBeenCalled();
  });

  it("says no fetches in this period when it was fetched before the window", () => {
    useSkillUsage.mockReturnValue(success({ ...none, firstFetchedOn: "2026-09-01" }));
    showContext({ onInstall: vi.fn() });
    expect(screen.getByText("No fetches in this period")).toBeInTheDocument();
    expect(screen.getByText("Fetches appear when someone resolves, downloads or reads this agent context through the API, the SDK or MCP.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install" })).not.toBeInTheDocument();
  });

  it("keeps the period wording when an older server doesn't report the first fetch", () => {
    useSkillUsage.mockReturnValue(success(none));
    showContext();
    expect(screen.getByText("No fetches in this period")).toBeInTheDocument();
  });

  it("shows a retryable error that says the context is unchanged", () => {
    const refetch = vi.fn();
    useSkillUsage.mockReturnValue({ isError: true, refetch });
    showContext();
    expect(screen.getByText("Could not load usage")).toBeInTheDocument();
    expect(screen.getByText("Your agent context and versions are unchanged.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
