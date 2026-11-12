/* eslint-disable react/prop-types */
// The classic Summary's More menu: Download…, Print or save as PDF and Copy
// summary, from the table the page shows (no DSX toolbar, no new request).
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const toast = vi.hoisted(() => vi.fn());
const mixedToolbar = vi.hoisted(() => vi.fn(() => null));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: requests,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/sections/common/ux/toast", async (original) => ({
  ...(await original()),
  default: toast,
  notify: toast,
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function GridProbe({ rowData }, ref) {
      return (
        <div ref={ref} data-testid="summary-grid">
          {JSON.stringify(rowData)}
        </div>
      );
    }),
  };
});
vi.mock(
  "src/sections/experiment-detail/ExperimentBarRightSection/ExperimentBarSummaryRightSection",
  () => ({ default: mixedToolbar }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentEvaluationChart",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentSummaryChart/SpiderChartExperiment",
  () => ({ default: () => null }),
);
/* eslint-enable react/prop-types */

import ClassicExperimentSummary from "../ClassicExperimentSummary";
import { summaryTable, summaryText } from "../classicSummaryExport";

const experimentId = "0b5b7b9e-2222-4c8f-9a8e-0e2f8d1d2c3e";
const baseUrl = `/model-hub/experiments/v2/${experimentId}/`;
const HEADERS = [
  "Experiment Name",
  "Average Response time (ms)",
  "Prompt Tokens",
  "Completion Tokens",
  "Total Tokens",
  "quality",
];
const CSV = [
  HEADERS.join(","),
  "Prompt A,245.3 ms,320,180.5,500.5,87%",
  '"Prompt, B",120 ms,300,100,400,40%',
].join("\n");
const TEXT = [
  "Support prompts · Experiment summary",
  [
    "Prompt A",
    "- Average Response time (ms): 245.3 ms",
    "- Prompt Tokens: 320",
    "- Completion Tokens: 180.5",
    "- Total Tokens: 500.5",
    "- quality: 87%",
  ].join("\n"),
  [
    "Prompt, B",
    "- Average Response time (ms): 120 ms",
    "- Prompt Tokens: 300",
    "- Completion Tokens: 100",
    "- Total Tokens: 400",
    "- quality: 40%",
  ].join("\n"),
].join("\n\n");

let tableData;
const clients = [];
const originalPrint = window.print;

// A Blob that keeps its text: jsdom's Blob has no reliable text() reader.
class RecordingBlob extends Blob {
  constructor(parts = [], options) {
    super(parts, options);
    this.content = parts.join("");
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  tableData = [
    {
      dataset_id: "a",
      experiment_dataset_name: "Prompt A",
      average_response_time: 245.34,
      prompt_token: 320,
      completion_token: 180.5,
      total_token: 500.5,
      quality: 86.6,
    },
    {
      dataset_id: "b",
      experiment_dataset_name: "Prompt, B",
      average_response_time: 120,
      prompt_token: 300,
      completion_token: 100,
      total_token: 400,
      quality: 40,
    },
  ];
  requests.get.mockImplementation(async (url) => {
    if (url === `${baseUrl}stats/`)
      return {
        data: {
          result: {
            metadata: { is_winner_chosen: false },
            table_data: tableData,
            column_config: [
              { id: "quality", name: "quality", output_type: "score" },
            ],
          },
        },
      };
    if (url === baseUrl)
      return {
        data: {
          status: true,
          result: {
            id: experimentId,
            name: "Support prompts",
            status: "Completed",
            runs: [],
            runs_total: 0,
            runs_has_more: false,
            runs_next: null,
          },
        },
      };
    throw new Error(`Unexpected GET in classic summary actions test: ${url}`);
  });
  // jsdom has no object URLs; left in place for saveFile's deferred revoke.
  URL.createObjectURL = vi.fn(() => "blob:classic-summary");
  URL.revokeObjectURL = vi.fn();
  vi.stubGlobal("Blob", RecordingBlob);
  window.print = vi.fn();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.print = originalPrint;
});

function mountSummary() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  renderWithRouter(
    <QueryClientProvider client={client}>
      <Routes>
        <Route
          path="/experiment/:experimentId/summary"
          element={<ClassicExperimentSummary />}
        />
      </Routes>
    </QueryClientProvider>,
    { route: `/experiment/${experimentId}/summary` },
  );
  return client;
}

async function openMore(client) {
  const more = await screen.findByRole("button", { name: "More" });
  // The detail read names the experiment (file name, print and copy title).
  await waitFor(() =>
    expect(client.getQueryState(["experiment", experimentId])?.status).toBe(
      "success",
    ),
  );
  await act(() => new Promise((done) => setTimeout(done, 20)));
  fireEvent.click(more);
  return screen.findByRole("menu");
}

describe("classic Summary More menu", () => {
  it("shows Download…, Print or save as PDF and Copy summary beside Choose winner", async () => {
    const client = mountSummary();
    const more = await screen.findByRole("button", { name: "More" });
    expect(more).toHaveAttribute("aria-haspopup", "menu");
    expect(screen.getByRole("button", { name: "Choose winner" })).toBeVisible();
    const menu = await openMore(client);
    const primary = (item) =>
      item.querySelector(".MuiListItemText-primary").textContent;
    expect(within(menu).getAllByRole("menuitem").map(primary)).toEqual([
      "Download…",
      "Print or save as PDF",
      "Copy summary",
    ]);
    for (const item of within(menu).getAllByRole("menuitem"))
      expect(item).not.toHaveAttribute("aria-disabled");
    // Only the two reads the Summary already makes; no DSX toolbar mounts.
    expect(new Set(requests.get.mock.calls.map(([url]) => url))).toEqual(
      new Set([`${baseUrl}stats/`, baseUrl]),
    );
    expect(requests.post).not.toHaveBeenCalled();
    expect(mixedToolbar).not.toHaveBeenCalled();
  });

  it("Copy summary copies the runs, evaluations and scores as shown, with the success toast", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const menu = await openMore(mountSummary());
    fireEvent.click(
      within(menu).getByRole("menuitem", { name: /^Copy summary/ }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText).toHaveBeenCalledWith(TEXT);
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith("Summary copied", {
        variant: "success",
        key: "classic-summary-copy",
      }),
    );
  });

  it("Download… saves the summary table as CSV with the on-screen header row", async () => {
    const names = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function () {
        names.push(this.download);
      },
    );
    const menu = await openMore(mountSummary());
    fireEvent.click(
      within(menu).getByRole("menuitem", { name: /^Download…/ }),
    );
    fireEvent.click(await screen.findByRole("menuitem", { name: /^CSV/ }));
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    const blob = URL.createObjectURL.mock.calls[0][0];
    expect(blob).toBeInstanceOf(RecordingBlob);
    expect(blob.type).toBe("text/csv");
    expect(blob.content.split("\n")[0]).toBe(HEADERS.join(","));
    expect(blob.content).toBe(CSV);
    expect(names).toHaveLength(1);
    expect(names[0]).toMatch(
      /^support-prompts-summary-\d{4}-\d{2}-\d{2}\.csv$/,
    );
    expect(toast).toHaveBeenCalledWith(
      expect.stringMatching(
        /^Saved support-prompts-summary-[\d-]+\.csv · 2 runs · \d+ B$/,
      ),
      { variant: "success", key: "classic-summary-download" },
    );
  });

  it("Download… also saves JSON keyed by the on-screen column names", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const menu = await openMore(mountSummary());
    fireEvent.click(
      within(menu).getByRole("menuitem", { name: /^Download…/ }),
    );
    fireEvent.click(await screen.findByRole("menuitem", { name: /^JSON/ }));
    const blob = URL.createObjectURL.mock.calls[0][0];
    expect(blob.type).toBe("application/json");
    const rows = JSON.parse(blob.content);
    expect(rows).toHaveLength(2);
    expect(Object.keys(rows[0])).toEqual(HEADERS);
    expect(rows[1]).toMatchObject({
      "Experiment Name": "Prompt, B",
      quality: "40%",
    });
  });

  it("Print or save as PDF opens the print view and calls window.print", async () => {
    const menu = await openMore(mountSummary());
    fireEvent.click(
      within(menu).getByRole("menuitem", { name: /^Print or save as PDF/ }),
    );
    expect(window.print).toHaveBeenCalledTimes(1);
    const view = screen.getByTestId("classic-summary-print");
    expect(view).toHaveAttribute("data-print-root");
    expect(
      within(view).getByText("Support prompts · Experiment summary"),
    ).toBeTruthy();
    expect(
      within(view)
        .getAllByRole("columnheader")
        .map((th) => th.textContent),
    ).toEqual(HEADERS);
    expect(
      within(view)
        .getAllByRole("row")
        .slice(1)
        .map((tr) => [...tr.cells].map((td) => td.textContent).join(",")),
    ).toEqual([
      "Prompt A,245.3 ms,320,180.5,500.5,87%",
      "Prompt, B,120 ms,300,100,400,40%",
    ]);
    // The view's own button prints again; Close leaves the Summary as it was.
    fireEvent.click(
      within(view).getByRole("button", { name: "Print or save as PDF" }),
    );
    expect(window.print).toHaveBeenCalledTimes(2);
    fireEvent.click(within(view).getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("classic-summary-print")).toBeNull();
    expect(screen.getByRole("button", { name: "Choose winner" })).toBeVisible();
  });

  it("the print view takes focus, and Escape gives it back to More", async () => {
    const menu = await openMore(mountSummary());
    fireEvent.click(
      within(menu).getByRole("menuitem", { name: /^Print or save as PDF/ }),
    );
    const view = screen.getByTestId("classic-summary-print");
    expect(view).toHaveAttribute("aria-modal", "true");
    await waitFor(() =>
      expect(document.activeElement).toBe(
        within(view).getByRole("button", { name: "Close" }),
      ),
    );
    fireEvent.keyDown(document.activeElement, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByTestId("classic-summary-print")).toBeNull(),
    );
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "More" }),
      ),
    );
  });

  it("keeps the three actions in place, disabled, while the summary has no runs", async () => {
    tableData = [];
    const menu = await openMore(mountSummary());
    const items = within(menu).getAllByRole("menuitem");
    expect(items).toHaveLength(3);
    for (const item of items)
      expect(item).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(items[1]);
    expect(window.print).not.toHaveBeenCalled();
    expect(screen.queryByTestId("classic-summary-print")).toBeNull();
  });
});

describe("classicSummaryExport", () => {
  it("reads each cell as the grid shows it", () => {
    const columns = [
      { field: "rank", headerName: "Ranking" },
      { field: "experiment_dataset_name", headerName: "Experiment Name" },
      {
        field: "average_response_time",
        headerName: "Average Response time (ms)",
        valueFormatter: ({ value }) => `${parseFloat(value.toFixed(1))} ms`,
      },
      { field: "score", headerName: "score", col: { output_type: "score" } },
      { field: "num", headerName: "num", col: { output_type: "numeric" } },
    ];
    const table = summaryTable(columns, [
      {
        rank: 1,
        experiment_dataset_name: "Prompt A",
        average_response_time: null,
        score: 0.9,
        num: 2.5,
      },
      { rank: 2, experiment_dataset_name: "Prompt B", score: 100, num: 3 },
    ]);
    expect(table.headers).toEqual([
      "Ranking",
      "Experiment Name",
      "Average Response time (ms)",
      "score",
      "num",
    ]);
    // Like ExperimentEvalCellRenderer: a score of at most 1 reads as a
    // fraction; a numeric eval prints as NumericCell does.
    expect(table.lines).toEqual([
      ["1", "Prompt A", "", "90%", "2.5000"],
      ["2", "Prompt B", "", "100%", "3"],
    ]);
    expect(table.nameIndex).toBe(1);
    expect(summaryText(table, "Summary")).toBe(
      [
        "Summary",
        "Prompt A\n- Ranking: 1\n- Average Response time (ms): —\n- score: 90%\n- num: 2.5000",
        "Prompt B\n- Ranking: 2\n- Average Response time (ms): —\n- score: 100%\n- num: 3",
      ].join("\n\n"),
    );
  });
});
