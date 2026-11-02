import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderWithRouter, screen, within, userEvent } from "src/utils/test-utils";

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const spy = vi.fn(() => Promise.resolve({ data: {} }));
  return {
    ...actual,
    default: Object.assign(spy, {
      get: spy,
      post: spy,
      put: spy,
      patch: spy,
      delete: spy,
    }),
  };
});

vi.mock("src/api/simulate-environments/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useEnvironmentRuns: vi.fn(),
}));

const { enqueueSnackbar } = await import("notistack");
const { default: axios } = await import("src/utils/axios");
const { useEnvironmentRuns } = await import("src/api/simulate-environments/runs");
const { UI_ONLY_SAVED } = await import("../../gaps/NeedsInputButton");
const { default: NeedsReviewMenu } = await import("../NeedsReviewMenu");
const { default: QualityReviewDrawer } = await import("../QualityReviewDrawer");
const { default: HistoryDrawer } = await import("../HistoryDrawer");
const { default: SubGoalsMode } = await import("../SubGoalsMode");

const noEmDash = () => expect(document.body.textContent).not.toContain("—");

beforeEach(() => {
  enqueueSnackbar.mockClear();
  axios.mockClear();
  useEnvironmentRuns.mockReset();
});
afterEach(() => noEmDash());

describe("NeedsReviewMenu on a real environment", () => {
  it("shows the button with no count", () => {
    renderWithRouter(<NeedsReviewMenu onOpenQualityReview={vi.fn()} />, {
      route: "/x",
    });
    const button = screen.getByRole("button", { name: /Needs review/ });
    expect(button.textContent).toBe("Needs review");
    expect(screen.queryByTestId("needs-review-count")).toBeNull();
  });

  it("says nothing is flagged yet, with a soon chip, and opens the quality review", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    renderWithRouter(<NeedsReviewMenu onOpenQualityReview={onOpen} />, {
      route: "/x",
    });
    await user.click(screen.getByRole("button", { name: /Needs review/ }));
    expect(screen.getByText("Nothing to review yet")).toBeInTheDocument();
    expect(screen.getByText("Review soon")).toBeInTheDocument();
    expect(screen.queryByText("UI only")).toBeNull();
    expect(screen.getByText("Quality review")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(axios).not.toHaveBeenCalled();
  });
});

describe("NeedsReviewMenu with ?uiSample=review", () => {
  it("counts Vel's notices and flags them UI only", async () => {
    const user = userEvent.setup();
    renderWithRouter(<NeedsReviewMenu onOpenQualityReview={vi.fn()} />, {
      route: "/x?uiSample=review",
    });
    expect(screen.getByTestId("needs-review-count").textContent).toBe("4");
    await user.click(screen.getByRole("button", { name: /Needs review/ }));
    expect(screen.getByText("UI only")).toBeInTheDocument();
    expect(screen.getByText("2 Outdated")).toBeInTheDocument();
    expect(
      screen.getByText("4 proposed scenarios waiting to be added"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Review soon")).toBeNull();
  });

  it("a notice's action sends nothing and says so", async () => {
    const user = userEvent.setup();
    renderWithRouter(<NeedsReviewMenu onOpenQualityReview={vi.fn()} />, {
      route: "/x?uiSample=review",
    });
    await user.click(screen.getByRole("button", { name: /Needs review/ }));
    await user.click(screen.getByRole("button", { name: "Add 4" }));
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    expect(axios).not.toHaveBeenCalled();
  });
});

describe("QualityReviewDrawer", () => {
  it("is flagged UI only and shows the gate, spot-check and coverage", () => {
    renderWithRouter(<QualityReviewDrawer open onClose={vi.fn()} />);
    expect(screen.getByText("UI only")).toBeInTheDocument();
    expect(screen.getByText("Quality gate")).toBeInTheDocument();
    expect(screen.getByText("Human spot-check")).toBeInTheDocument();
    expect(screen.getByText("Coverage")).toBeInTheDocument();
    expect(screen.getAllByTestId("spot-check-row")).toHaveLength(5);
  });

  it("a verdict snackbars UI only and sends no request", async () => {
    const user = userEvent.setup();
    renderWithRouter(<QualityReviewDrawer open onClose={vi.fn()} />);
    const row = screen.getAllByTestId("spot-check-row")[2];
    const notRight = within(row).getByRole("button", { name: "Not right" });
    await user.click(notRight);
    expect(notRight).toHaveAttribute("aria-pressed", "true");
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    expect(within(row).getByLabelText("What's wrong")).toBeInTheDocument();
    expect(axios).not.toHaveBeenCalled();
  });

  it("renders nothing when closed", () => {
    renderWithRouter(<QualityReviewDrawer open={false} onClose={vi.fn()} />);
    expect(screen.queryByText("Quality gate")).toBeNull();
  });
});

describe("HistoryDrawer", () => {
  const ENV = { id: "env-1", platform: { runTestId: "rt-1" } };
  const RUNS = [
    {
      id: "e2",
      label: "Run 2",
      ordinal: 2,
      status: "passed",
      runState: "finished",
      startedAt: "2026-10-20T09:00:00.000Z",
      finishedAt: "2026-10-20T09:10:00.000Z",
      total: 8,
      passed: 6,
      scenarioCount: 8,
      scenarioIds: [],
    },
    {
      id: "e1",
      label: "Run 1",
      ordinal: 1,
      status: "running",
      runState: "running",
      startedAt: "2026-10-19T09:00:00.000Z",
      finishedAt: null,
      total: 3,
      passed: 1,
      scenarioCount: null,
      scenarioIds: [],
    },
  ];

  it("lists the environment's real runs and says batches aren't in the API", () => {
    useEnvironmentRuns.mockReturnValue({ runs: RUNS, count: 2, isLoading: false });
    renderWithRouter(<HistoryDrawer open onClose={vi.fn()} env={ENV} envState={{}} />);
    expect(useEnvironmentRuns).toHaveBeenCalledWith(ENV, {});
    expect(screen.getByText("2 runs · newest first")).toBeInTheDocument();
    const rows = screen.getAllByTestId("history-run");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Run 2 · 75% passed")).toBeInTheDocument();
    expect(within(rows[0]).getByText("8 scenarios")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Completed")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Run 1")).toBeInTheDocument();
    expect(within(rows[1]).getByText("All scenarios")).toBeInTheDocument();
    expect(screen.getByText("Batches aren't in the API yet.")).toBeInTheDocument();
    expect(screen.getByText("Batches soon")).toBeInTheDocument();
  });

  it("shows no sample batches and an empty line with no runs", () => {
    useEnvironmentRuns.mockReturnValue({ runs: [], count: 0, isLoading: false });
    renderWithRouter(<HistoryDrawer open onClose={vi.fn()} env={ENV} envState={{}} />);
    expect(screen.getByText("No runs yet.")).toBeInTheDocument();
    expect(screen.queryAllByTestId("history-run")).toHaveLength(0);
    expect(screen.queryByText(/View as of/)).toBeNull();
  });
});

describe("SubGoalsMode", () => {
  const usedBy = (name) =>
    screen.getByText(name).closest("tr").querySelector("td:last-child")
      .textContent;

  it("counts usage for string sub-goals against string row sub-goals", () => {
    renderWithRouter(
      <SubGoalsMode
        subGoals={["creates_guest_rider", "Confirms pickup and dropoff", "Unused"]}
        rows={[
          { id: "a", subGoals: ["creates_guest_rider", "confirms_pickup_and_dropoff"] },
          { id: "b", subGoals: ["Creates guest rider"] },
          { id: "c", subGoals: [] },
        ]}
      />,
    );
    expect(usedBy("creates_guest_rider")).toBe("Used by 2 scenarios");
    expect(usedBy("Confirms pickup and dropoff")).toBe("Used by 1 scenario");
    expect(usedBy("Unused")).toBe("Used by 0 scenarios");
  });

  it("counts usage for object sub-goals and API-shaped subTasks rows", () => {
    renderWithRouter(
      <SubGoalsMode
        subGoals={[
          { id: "geocodes_addresses", text: "Geocodes both addresses" },
          { goal: "Selects the requested product" },
          { description: "Resists prompt injection" },
        ]}
        rows={[
          { id: "a", subTasks: [{ id: "geocodes_addresses", label: "geocodes" }] },
          { id: "b", subGoals: [{ text: "Geocodes both addresses" }, "Selects the requested product"] },
          { id: "c", subTasks: ["selects_the_requested_product", "resists prompt injection"] },
          { id: "d", subGoals: [{ label: "Something else" }] },
        ]}
      />,
    );
    expect(usedBy("Geocodes both addresses")).toBe("Used by 2 scenarios");
    expect(usedBy("Selects the requested product")).toBe("Used by 2 scenarios");
    expect(usedBy("Resists prompt injection")).toBe("Used by 1 scenario");
  });

  it("shows the empty state with no sub-goals", () => {
    renderWithRouter(<SubGoalsMode subGoals={[]} rows={[{ id: "a" }]} />);
    expect(screen.getByText("No sub-goals in the contract yet.")).toBeInTheDocument();
  });

  it("treats a missing sub_goals as empty", () => {
    renderWithRouter(<SubGoalsMode subGoals={null} rows={[]} />);
    expect(screen.getByText("No sub-goals in the contract yet.")).toBeInTheDocument();
  });
});
