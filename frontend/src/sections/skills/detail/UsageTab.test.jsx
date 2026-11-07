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
import UsageTab from "./UsageTab";

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
      expect(screen.getByText("Versions in use")).toBeInTheDocument();
      expect(
        screen.getByText(
          "Counts requests that fetched this skill. A fetch doesn't prove an agent used it.",
        ),
      ).toBeInTheDocument();
    },
  );
  it("queries every supported time range", async () => {
    show();
    expect(useSkillUsage).toHaveBeenLastCalledWith("skill-a", 30);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Time range" }));
    fireEvent.click(await screen.findByRole("option", { name: "Last 7 days" }));
    expect(useSkillUsage).toHaveBeenLastCalledWith("skill-a", 7);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Time range" }));
    fireEvent.click(
      await screen.findByRole("option", { name: "Last 90 days" }),
    );
    expect(useSkillUsage).toHaveBeenLastCalledWith("skill-a", 90);
  });
  it("provides exact daily counts and a consumer table without relying on chart colors", () => {
    show();
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
    expect(consumers.getByRole("cell", { name: "api" })).toBeInTheDocument();
    expect(consumers.getByRole("cell", { name: "9" })).toBeInTheDocument();
  });
  it("renders zero usage honestly and keeps the fetch caveat visible", () => {
    useSkillUsage.mockReturnValue(
      success({ ...usage, total: 0, byDay: [], byVersion: [], consumers: [] }),
    );
    show();
    expect(screen.getByText("No fetches in this period")).toBeInTheDocument();
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
  it("retries actual API failures", () => {
    const refetch = vi.fn();
    useSkillUsage.mockReturnValue({ isError: true, refetch });
    show();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});

vi.mock("src/sections/skills/governance/AdoptionPanel", () => ({ default: () => <div>Adoption panel</div> }));
