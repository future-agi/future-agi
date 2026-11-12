import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import CategoryExplorer from "../CategoryExplorer";
import { ReportProvider } from "../reportContext";
import { collectRunUnits, useRunUnits } from "../insightsApi";
import { readHandoff } from "src/sections/projects/observeHandoff";

vi.mock("src/components/iconify", () => ({ default: () => null }));
const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock("react-router", () => ({ useNavigate: () => navigate }));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { INSTANT_EVAL: "instant_eval" },
  useFeatureDenied: () => false,
}));
vi.mock("../insightsApi", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunUnits: vi.fn(),
  collectRunUnits: vi.fn(),
}));

const theme = createTheme({ palette: palette("light"), typography });

const category = (count) => ({
  id: "cat-refunds",
  name: "Refund asked twice",
  description: "The user had to ask for the refund again.",
  level: 1,
  parent_id: null,
  stats: { count, share: 0.4 },
});

const report = (cat) => ({
  projectId: "project-1",
  runWindow: null,
  categoriesById: new Map([[cat.id, cat]]),
  tree: [],
  colorOf: () => "#888",
  openCategory: vi.fn(),
  openExample: vi.fn(),
  refIndex: new Map(),
  previews: new Map(),
});

const run = (unit, scope = {}) => ({
  id: "run-1",
  unit,
  scope,
  result: { totals: {} },
});

const renderExplorer = (count, unit = "traces", scope = {}) => {
  const cat = category(count);
  useRunUnits.mockReturnValue({
    data: {
      units: [
        { unit_id: "t-1", summary: "Asked for the refund twice." },
        { unit_id: "t-2", summary: "Asked again after no reply." },
      ],
      total: count,
    },
    isLoading: false,
    isError: false,
    isPlaceholderData: false,
    refetch: vi.fn(),
  });
  return render(
    <ThemeProvider theme={theme}>
      <ReportProvider value={report(cat)}>
        <CategoryExplorer
          categoryId={cat.id}
          reportName="Weekly failures"
          run={run(unit, scope)}
          onBack={() => {}}
        />
      </ReportProvider>
    </ThemeProvider>,
  );
};

const openMore = () =>
  fireEvent.click(
    screen.getByRole("button", { name: "More actions for this category" }),
  );

describe("CategoryExplorer grid action (W10-INSIGHTS-U04)", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("says the grid opens only the first 200 when the category has more", () => {
    renderExplorer(412);
    openMore();

    expect(screen.getByText("Open first 200 in grid")).toBeInTheDocument();
    expect(
      screen.getByText(
        "This category has 412 traces; the grid opens the first 200.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Open all in grid")).not.toBeInTheDocument();
  });

  it("keeps 'Open all in grid' when every item fits in the grid", () => {
    renderExplorer(40);
    openMore();

    expect(screen.getByText("Open all in grid")).toBeInTheDocument();
    expect(
      screen.getByText("The Traces view filtered to these"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Open first 200 in grid"),
    ).not.toBeInTheDocument();
  });
});

// OBS-SW-143: a report's category handed over to a new report keeps the
// dates the report read, so the new one reads each user's activity there.
describe("CategoryExplorer Find patterns inside", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  const WINDOW = ["2026-10-01T00:00:00+00:00", "2026-10-08T00:00:00+00:00"];
  const handedOver = () => {
    const [[href]] = navigate.mock.calls;
    const key = new URL(href, "http://localhost").searchParams.get("seed");
    return readHandoff(key);
  };
  const findPatterns = async () => {
    openMore();
    fireEvent.click(screen.getByText("Find patterns inside"));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
  };

  it("hands the category's users over with the window the report read", async () => {
    collectRunUnits.mockResolvedValue([
      { unit_id: "user-1", time: "2026-10-02T10:00:00Z", user_id: "user-1" },
    ]);
    renderExplorer(1, "users", { preset: "Custom", window: WINDOW });
    await findPatterns();
    expect(handedOver()).toMatchObject({
      unit: "users",
      population: [
        { id: "user-1", time: "2026-10-02T10:00:00Z", user_id: "user-1" },
      ],
      source: "Weekly failures",
      sourceWindow: WINDOW,
    });
  });

  it("hands over no window from a report that read none", async () => {
    collectRunUnits.mockResolvedValue([{ unit_id: "t-1" }]);
    renderExplorer(1, "traces");
    await findPatterns();
    expect(handedOver()).toMatchObject({ unit: "traces" });
    expect(handedOver()).not.toHaveProperty("sourceWindow");
  });
});
