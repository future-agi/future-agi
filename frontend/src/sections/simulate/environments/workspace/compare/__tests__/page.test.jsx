import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";

import { ENV, RUNS, environmentResult, runsResult } from "./compareFixtures";
import { blobText, renderComparePage as renderPage } from "./renderComparePage";

const useEnvironment = vi.fn();
vi.mock(
  "src/api/simulate-environments/environment",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useEnvironment: (...args) => useEnvironment(...args),
  }),
);

const useEnvironmentRuns = vi.fn();
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useEnvironmentRuns: (...args) => useEnvironmentRuns(...args),
}));

// The calls behind the table; the header tests don't need any.
const fetchRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runCalls", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchRunCalls: (...args) => fetchRunCalls(...args),
}));

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => enqueueSnackbar(...args),
}));

beforeEach(() => {
  useEnvironment.mockReturnValue(environmentResult());
  useEnvironmentRuns.mockReturnValue(runsResult());
  fetchRunCalls.mockResolvedValue({
    results: [],
    count: 0,
    total_pages: 1,
    evaluation_columns: [],
  });
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("Compare runs header", () => {
  it("flags the whole page as UI only", () => {
    renderPage("?runs=r1,r3");
    expect(screen.getAllByText("UI only")).toHaveLength(1);
  });

  it('?runs=a,b renders pills "Run N · agent vX · P% p/t"', () => {
    renderPage("?runs=r1,r3");
    const pills = screen.getAllByTestId("compare-run-pill");
    expect(pills).toHaveLength(2);
    expect(pills[0]).toHaveTextContent("Run 1 · agent v1 · 60% 6/10");
    expect(pills[0]).toHaveTextContent("baseline");
    expect(pills[1]).toHaveTextContent("Run 3 · agent v2 · 75% 9/12");
    expect(pills[1]).not.toHaveTextContent("baseline");
    expect(screen.getByText(ENV.name)).toBeInTheDocument();
  });

  it("Add run lists the environment's runs", () => {
    renderPage("?runs=r1,r3");
    fireEvent.click(screen.getByRole("button", { name: /Add run/ }));
    const menu = screen.getByRole("menu");
    // Only the runs not already in the comparison.
    expect(within(menu).getByText("Run 2 · agent v2")).toBeInTheDocument();
    expect(within(menu).getByText("67% 8/12")).toBeInTheDocument();
    expect(
      within(menu).queryByText(/Run 3 · agent v2/),
    ).not.toBeInTheDocument();

    fireEvent.click(within(menu).getByText("Run 2 · agent v2"));
    fireEvent.click(within(menu).getByRole("button", { name: "Add 1" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/simulate/environments/${ENV.id}/compare?runs=r1%2Cr3%2Cr2`,
    );
  });

  it("subtitle reads runs, agent versions, scenarios in every run, repeats differ", () => {
    renderPage("?runs=r1,r3");
    expect(
      screen.getByText(
        "2 runs · 2 agent versions · 2 scenarios in every run · 1 in only some · repeats per scenario differ (1, 3)",
      ),
    ).toBeInTheDocument();
  });

  it("Export downloads the comparison JSON; Copy link copies the URL", async () => {
    let exported = null;
    const createObjectURL = vi.fn((blob) => {
      exported = blob;
      return "blob:compare";
    });
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const writeText = vi.fn().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    renderPage("?runs=r1,r3");
    await screen.findByText("No calls yet");
    fireEvent.click(screen.getByRole("button", { name: "Export comparison" }));
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:compare");
    const payload = JSON.parse(await blobText(exported));
    expect(payload.environment).toEqual({ id: ENV.id, name: ENV.name });
    expect(payload.baseline).toBe("r1");
    expect(payload.runs.map((r) => [r.id, r.run, r.agentVersion])).toEqual([
      ["r1", "Run 1", "v1"],
      ["r3", "Run 3", "v2"],
    ]);
    expect(payload.runs[1]).toMatchObject({
      passed: 9,
      total: 12,
      passRate: 75,
    });

    fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(window.location.href);
    expect(enqueueSnackbar).toHaveBeenCalledWith("Link copied", {
      variant: "success",
    });
  });

  it("other environments section says it isn't in the API yet", async () => {
    renderPage("?runs=r1,r3");
    fireEvent.click(screen.getByRole("button", { name: /Add run/ }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("Other environments")).toBeInTheDocument();
    fireEvent.mouseOver(within(menu).getByText("Other environments soon"));
    expect(
      await screen.findByText(
        "Runs from other environments aren't in the API yet",
      ),
    ).toBeInTheDocument();
  });

  it("asks for two runs when the link has fewer", () => {
    renderPage("?runs=r1");
    expect(screen.getByText("Nothing to compare")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to runs" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/simulate/environments/${ENV.id}?tab=runs`,
    );
  });

  it("says the environment has no runs to compare yet", () => {
    useEnvironmentRuns.mockReturnValue(runsResult([RUNS[2]]));
    renderPage("?runs=r1,r3");
    expect(screen.getByText("Not yet run")).toBeInTheDocument();
  });
});
