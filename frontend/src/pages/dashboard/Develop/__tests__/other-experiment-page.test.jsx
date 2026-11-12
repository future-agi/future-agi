/* eslint-disable react/prop-types */
// An experiment that opens in the original results view, as shipped
// (unreleased parts shown): its page is the Data and Summary page the
// production build showed, mounted as it is. Data mounts its Data host (the
// Data view, which reads the rows, is a stand-in here; the real one's rows
// read is in other-experiment-data-rows.test.jsx); Summary reads its stats
// and compares its variants (table, charts, Choose winner), as that page
// did. Sub-paths of the new page land on Data.
// The detail is read once. An experiment with no snapshot dataset (the
// sample experiment every organization gets) has no stats: its Summary says
// so in one plain sentence instead of an error, and its Data host still
// mounts.
//
// A failure names what is on screen instead (a render error, the deciding
// skeleton, the new page), and a control case mounts the original page
// directly, so a red here tells a product defect from a test one. Any read
// besides the detail and the stats is refused at once, with the read and the
// line that made it in the error, so no case waits on an answer that never
// comes.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const unreleased = vi.hoisted(() => ({ shown: true }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: requests,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function GridProbe(_props, ref) {
      return <div ref={ref} />;
    }),
  };
});
// The summary table and charts have their own tests (l3-classic-wrapper,
// classic-summary-actions); here they only show what the page gave them.
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentSummaryTable",
  () => ({
    default: ({ rows }) => (
      <div data-testid="summary-table">
        {rows.map((row) => row.experiment_dataset_name).join(" | ")}
      </div>
    ),
  }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentEvaluationChart",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentSummaryChart/SpiderChartExperiment",
  () => ({ default: () => null }),
);
// The page header and tabs, and the Data grid, have their own tests; here
// they only mark that the page mounted them.
vi.mock("src/sections/experiment-detail-classic/ExperimentRow", () => ({
  default: () => <div data-testid="experiment-header" />,
}));
vi.mock("src/sections/experiment-detail-classic/ExperimentBar", () => ({
  default: () => <div data-testid="experiment-tabs" />,
}));
vi.mock(
  "src/sections/experiment-detail-classic/ExperimentData/ExperimentDataView",
  () => ({ default: () => <div data-testid="experiment-data-grid" /> }),
);
// The new page is not under test: it only marks that it mounted.
vi.mock("src/sections/experiment-detail/ExperimentWrapperView", () => ({
  default: () => <div data-testid="new-experiment-page" />,
}));

import ExperimentWrapper from "src/pages/dashboard/Develop/ExperimentWrapper";
// Loaded up front: the module the page's lazy import resolves to, also used
// by the control case.
import OriginalPage from "src/sections/experiment-detail-classic/ExperimentWrapperView";

const ID = "0b5b7b9e-3333-4c8f-9a8e-0e2f8d1d2c3e";
const DATASET = "11111111-1111-4111-8111-111111111111";
const DETAIL_URL = `/model-hub/experiments/v2/${ID}/`;
const STATS_URL = `/model-hub/experiments/v2/${ID}/stats/`;
const NOT_AVAILABLE = "A summary is not available for this experiment.";
const READ_FAILED = "Could not load the experiment summary. Try again.";
const WAIT = { timeout: 10000 };

const detailOf = ({ name, snapshot }) => ({
  data: {
    status: true,
    result: {
      id: ID,
      dataset_id: DATASET,
      name,
      status: "Completed",
      snapshot_dataset_id: snapshot,
      runs: [],
      runs_total: 0,
      runs_has_more: false,
      runs_next: null,
      results_availability: {
        reason: "native_identity_not_recorded",
        classic_url: `/dashboard/develop/experiment/${ID}/data`,
      },
      opens_in_full_results: false,
    },
  },
});
// Made by the production create: it has a snapshot dataset, so its stats read.
const PRODUCTION_EXPERIMENT = detailOf({
  name: "Support prompts, March",
  snapshot: "22222222-2222-4222-8222-222222222222",
});
// The sample experiment every organization gets (accounts/user_onboard.py):
// no snapshot dataset.
const SAMPLE_EXPERIMENT = detailOf({
  name: "Response Generation",
  snapshot: null,
});
// What the production build's Summary reads for it: one row per variant, one
// column per evaluation.
const STATS = {
  data: {
    result: {
      metadata: { is_winner_chosen: false },
      table_data: [
        {
          dataset_id: "a",
          experiment_dataset_name: "Support prompt-gpt-4o-mini",
          average_response_time: 245.3,
          prompt_token: 320,
          completion_token: 180,
          total_token: 500,
          quality: 86.6,
        },
        {
          dataset_id: "b",
          experiment_dataset_name: "Support prompt-claude-haiku",
          average_response_time: 120,
          prompt_token: 300,
          completion_token: 100,
          total_token: 400,
          quality: 40,
        },
      ],
      column_config: [{ id: "quality", name: "quality", output_type: "score" }],
    },
  },
};
const NO_SNAPSHOT_400 = Object.assign(new Error("Request failed (400)"), {
  response: {
    status: 400,
    data: {
      status: false,
      result:
        "This experiment has no snapshot dataset. Use the v1 stats endpoint.",
    },
  },
  statusCode: 400,
});

// Any render error under the page shows here instead of an empty screen.
class RenderError extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return <pre data-testid="render-error">{String(this.state.error)}</pre>;
  }
}

let client;
function renderPage(element, sub) {
  client = new QueryClient({
    // As the app (app.jsx): fresh for 5 s, so the page reuses the detail
    // read that chose it.
    defaultOptions: {
      queries: { retry: false, staleTime: 5000 },
      mutations: { retry: false },
    },
  });
  renderWithRouter(
    <QueryClientProvider client={client}>
      <HelmetProvider>
        <RenderError>
          <Routes>
            <Route
              path="/dashboard/develop/experiment/:experimentId/*"
              element={element}
            />
          </Routes>
        </RenderError>
      </HelmetProvider>
    </QueryClientProvider>,
    { route: `/dashboard/develop/experiment/${ID}/${sub}` },
  );
}
const openAt = (sub) => renderPage(<ExperimentWrapper />, sub);

const getsOf = (url) =>
  requests.get.mock.calls.filter(([called]) => called === url).length;

// Any other read is refused at once and recorded with the first source line
// that made it; a failure to find the page lists them.
let otherReads = [];
function callerOf(stack) {
  const frame = String(stack)
    .split("\n")
    .slice(1)
    .find(
      (line) =>
        line.includes("/src/") &&
        !line.includes("node_modules") &&
        !line.includes("other-experiment-page.test"),
    );
  if (!frame) return "an unknown caller";
  const at = frame.match(/(src\/[^\s()?]+?:\d+)/);
  return at ? at[1] : frame.trim();
}
const describeOtherReads = () =>
  otherReads.map(({ url, from }) => `${url} from ${from}`).join("; ");
const answerWith = (detail) =>
  requests.get.mockImplementation(async (url) => {
    if (url === DETAIL_URL) return detail;
    if (url === STATS_URL) {
      const snapshot = detail.data.result.snapshot_dataset_id;
      if (snapshot === null) throw NO_SNAPSHOT_400;
      return STATS;
    }
    const read = { url: String(url), from: callerOf(new Error().stack) };
    if (otherReads.length < 10) otherReads.push(read);
    throw new Error(`Unexpected GET ${read.url} from ${read.from}`);
  });

// What is on screen instead of the original page, for a failure message.
function whatIsShown() {
  const crashed = screen.queryByTestId("render-error");
  if (crashed) return `a render error: ${crashed.textContent}`;
  if (screen.queryByTestId("new-experiment-page")) return "the new page";
  if (screen.queryByText("Experiment not found")) return "the not-found page";
  if (screen.queryByTestId("experiment-page-loading")) {
    const state = client.getQueryState(["experiment", ID]);
    return state?.status === "success"
      ? "the skeleton after the detail answered (the page's lazy load)"
      : "the skeleton before the detail answered";
  }
  return "nothing";
}
async function findOnPage(find) {
  try {
    return await find();
  } catch {
    const reads = otherReads.length
      ? `; refused reads: ${describeOtherReads()}`
      : "";
    throw new Error(
      `The original page is not shown; on screen: ${whatIsShown()}${reads}`,
    );
  }
}
const pageTestId = (testId) =>
  findOnPage(() => screen.findByTestId(testId, {}, WAIT));

beforeEach(() => {
  vi.clearAllMocks();
  requests.get.mockReset();
  otherReads = [];
  unreleased.shown = true;
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
});
afterEach(() => {
  cleanup();
  client?.clear();
  setLaunchOverrideForTest(null);
});

describe("the original page itself, mounted directly (control)", () => {
  beforeEach(() => {
    answerWith(PRODUCTION_EXPERIMENT);
  });

  it("shows its header, tabs and Data host", async () => {
    renderPage(<OriginalPage />, "data");
    expect(await pageTestId("experiment-data-grid")).toBeVisible();
    expect(screen.getByTestId("experiment-header")).toBeVisible();
    expect(screen.getByTestId("experiment-tabs")).toBeVisible();
  });
});

describe("an experiment made on the production build, as shipped", () => {
  beforeEach(() => {
    answerWith(PRODUCTION_EXPERIMENT);
  });

  it("Summary reads its stats and compares its variants, with Choose winner, as production did", async () => {
    openAt("summary");
    const table = await pageTestId("summary-table");
    expect(table).toHaveTextContent(
      "Support prompt-gpt-4o-mini | Support prompt-claude-haiku",
    );
    expect(screen.queryByTestId("new-experiment-page")).toBeNull();
    const chooseWinner = screen.getByRole("button", { name: "Choose winner" });
    expect(chooseWinner).toBeEnabled();
    await waitFor(() => {
      const state = client.getQueryState(["experiment", ID]);
      expect(state?.status).toBe("success");
    });
    // The detail chose the page and served it: read once. Summary reads its
    // stats (again once the detail shows the experiment finished).
    expect(getsOf(DETAIL_URL)).toBe(1);
    expect(getsOf(STATS_URL)).toBeGreaterThan(0);
    expect(requests.post).not.toHaveBeenCalled();
  });

  it("Data mounts the Data host with its header and tabs, reading the detail once", async () => {
    openAt("data");
    expect(await pageTestId("experiment-data-grid")).toBeVisible();
    expect(screen.getByTestId("experiment-header")).toBeVisible();
    expect(screen.getByTestId("experiment-tabs")).toBeVisible();
    expect(screen.queryByTestId("new-experiment-page")).toBeNull();
    expect(getsOf(DETAIL_URL)).toBe(1);
  });

  it.each(["matrix", "paths", "traces"])(
    "a bookmarked %s page lands on Data, as on production",
    async (sub) => {
      openAt(sub);
      expect(await pageTestId("experiment-data-grid")).toBeVisible();
      expect(screen.queryByTestId("new-experiment-page")).toBeNull();
    },
  );
});

describe("the sample experiment every organization gets (no snapshot)", () => {
  beforeEach(() => {
    answerWith(SAMPLE_EXPERIMENT);
  });

  it("Summary says in one sentence that no summary is available, with no error and no stats read", async () => {
    openAt("summary");
    const line = await findOnPage(() =>
      screen.findByText(NOT_AVAILABLE, {}, WAIT),
    );
    expect(line).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(NOT_AVAILABLE);
    expect(screen.queryByText(READ_FAILED)).toBeNull();
    expect(screen.queryByText(/no snapshot dataset/i)).toBeNull();
    expect(screen.queryByText(/400/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("new-experiment-page")).toBeNull();
    expect(getsOf(STATS_URL)).toBe(0);
    expect(getsOf(DETAIL_URL)).toBe(1);
  });

  it("Data still mounts the Data host, without the Summary sentence", async () => {
    openAt("data");
    expect(await pageTestId("experiment-data-grid")).toBeVisible();
    expect(screen.getByTestId("experiment-tabs")).toBeVisible();
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull();
  });
});
