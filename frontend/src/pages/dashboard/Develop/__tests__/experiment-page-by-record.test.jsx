/* eslint-disable react/prop-types */
// Which page an experiment opens on, read from the experiment's own detail.
// With unreleased parts shown, the detail's opens_in_full_results decides:
// false (the new page could not show every variant: created before runs were
// recorded, or a variant with no counted run) opens the original Data and
// Summary page that reads its rows and stats; true opens the new page; a
// detail without the field is never guessed into the original page; an id
// that does not exist gets the not-found page. The detail is read once, under
// the key both pages share, and the page it chose stays mounted when that
// page reads the detail again. A failed read that is not a refusal or a
// missing id opens the new page only until a later detail decides. With
// unreleased parts hidden the page is chosen as before, by the build.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

// Both switch states, the way the launch tests pin them: the override that
// launchSwitch reads and the export the product modules read.
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
const mode = vi.hoisted(() => ({ prototype: false }));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => mode.prototype,
}));
const requests = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: requests,
}));

// Each page stands in with the detail read it really makes on mount. The new
// page reads with the query's default retryOnMount, so after a failed read it
// reads again when it mounts, as the real page does.
vi.mock("src/sections/experiment-detail/ExperimentWrapperView", async () => {
  const { useExperimentDetail } = await import("src/api/develop/runs");
  const { default: NotFound } = await import(
    "src/sections/experiment-detail/ExperimentNotFound"
  );
  return {
    default: function NewPage() {
      const { experimentId } = useParams();
      const detail = useExperimentDetail(experimentId, { retry: false });
      if (detail.isError && detail.error?.response?.status === 404) {
        return <NotFound experimentId={experimentId} />;
      }
      return (
        <div data-testid="new-experiment-page">
          {detail.isError ? "refused" : detail.data?.name ?? ""}
        </div>
      );
    },
  };
});
vi.mock(
  "src/sections/experiment-detail-classic/ExperimentWrapperView",
  async () => {
    const { useGetExperimentDetails } = await import(
      "src/hooks/useGetExperimentDetails"
    );
    return {
      default: function DataAndSummaryPage() {
        const { experimentId } = useParams();
        const detail = useGetExperimentDetails(experimentId);
        return (
          <div data-testid="data-and-summary-page">
            {detail.data?.name ?? ""}
          </div>
        );
      },
    };
  },
);

import ExperimentWrapper from "src/pages/dashboard/Develop/ExperimentWrapper";

const ID = "6b0d0f5e-1111-4a8b-9c3d-2e2f8d1d2c3e";
const DETAIL_URL = `/model-hub/experiments/v2/${ID}/`;
const BEFORE_RUNS = "native_identity_not_recorded";
const NOT_FOUND_LINE = `No experiment has the id ${ID}.`;

const detail = ({ name, reason, full, runs = [] }) => ({
  data: {
    status: true,
    result: {
      id: ID,
      dataset_id: "11111111-1111-4111-8111-111111111111",
      name,
      status: "Completed",
      runs,
      runs_total: runs.length,
      runs_has_more: false,
      runs_next: null,
      results_availability: {
        reason,
        classic_url: `/dashboard/develop/experiment/${ID}/data`,
      },
      ...(full === undefined ? {} : { opens_in_full_results: full }),
    },
  },
});
const OLDER = detail({
  name: "Support prompts, March",
  reason: BEFORE_RUNS,
  full: false,
});
const WITH_RUNS = detail({
  name: "Support prompts, runs",
  reason: null,
  full: true,
  runs: [{ run_id: "r1", experiment_id: ID }],
});
// Created on this build with one versioned and one unversioned prompt: the
// versioned variant's run exists (so no results_availability reason), the
// other variant has none, so the new page could not show it.
const MIXED = detail({
  name: "Support prompts, mixed",
  reason: null,
  full: false,
  runs: [{ run_id: "r2", experiment_id: ID }],
});
// A detail from a backend that does not send the decision.
const UNMARKED = detail({
  name: "Support prompts, unmarked",
  reason: BEFORE_RUNS,
});
const httpError = (status) =>
  Object.assign(new Error(`Request failed with status ${status}`), {
    response: {
      status,
      data: { status: false, result: "Experiment not found" },
    },
    statusCode: status,
  });

const detailReads = () =>
  requests.get.mock.calls.filter(([url]) => url === DETAIL_URL).length;

let client;
function openAt(path) {
  // The app's queries stay fresh for 5 s (app.jsx), so a page that mounts
  // after the decision reads the cached detail instead of asking again.
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5000 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <HelmetProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/dashboard/develop" element={<div>datasets</div>} />
            <Route
              path="/dashboard/develop/experiment/:experimentId/*"
              element={<ExperimentWrapper />}
            />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>
    </QueryClientProvider>,
  );
}

const answer = (response) =>
  requests.get.mockImplementation(async (url) => {
    if (url === DETAIL_URL) {
      if (response instanceof Error) throw response;
      return response;
    }
    throw new Error(`Unexpected GET: ${url}`);
  });

beforeEach(() => {
  requests.get.mockReset();
  mode.prototype = false;
});
afterEach(() => {
  cleanup();
  client?.clear();
  setLaunchOverrideForTest(null);
});

describe("experiment page with unreleased parts shown (as shipped)", () => {
  beforeEach(() => {
    unreleased.shown = true;
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  });

  it.each(["data", "summary", ""])(
    "opens a production experiment on the Data and Summary page (/%s)",
    async (sub) => {
      answer(OLDER);
      openAt(`/dashboard/develop/experiment/${ID}/${sub}`);
      const page = await screen.findByTestId("data-and-summary-page");
      expect(page).toHaveTextContent("Support prompts, March");
      expect(screen.queryByTestId("new-experiment-page")).toBeNull();
      expect(detailReads()).toBe(1);
    },
  );

  it("opens an experiment with a variant the new page cannot show on the Data and Summary page, never in part", async () => {
    answer(MIXED);
    openAt(`/dashboard/develop/experiment/${ID}/data?run=r2`);
    const page = await screen.findByTestId("data-and-summary-page");
    expect(page).toHaveTextContent("Support prompts, mixed");
    expect(screen.queryByTestId("new-experiment-page")).toBeNull();
    expect(detailReads()).toBe(1);
  });

  it.each([
    ["the new page can show in full", WITH_RUNS, "Support prompts, runs"],
    [
      "whose detail does not say (never guessed)",
      UNMARKED,
      "Support prompts, unmarked",
    ],
  ])(
    "opens an experiment %s on the new page",
    async (_label, response, name) => {
      answer(response);
      openAt(`/dashboard/develop/experiment/${ID}/data`);
      const page = await screen.findByTestId("new-experiment-page");
      expect(page).toHaveTextContent(name);
      expect(screen.queryByTestId("data-and-summary-page")).toBeNull();
      expect(detailReads()).toBe(1);
    },
  );

  it("shows a skeleton, and neither page, until the detail answers", async () => {
    requests.get.mockImplementation(() => new Promise(() => {}));
    openAt(`/dashboard/develop/experiment/${ID}/data`);
    const skeleton = await screen.findByTestId("experiment-page-loading");
    expect(skeleton).toBeVisible();
    expect(screen.queryByTestId("new-experiment-page")).toBeNull();
    expect(screen.queryByTestId("data-and-summary-page")).toBeNull();
  });

  it("keeps the not-found page for an id that does not exist", async () => {
    answer(httpError(404));
    openAt(`/dashboard/develop/experiment/${ID}/data`);
    expect(await screen.findByText("Experiment not found")).toBeVisible();
    expect(screen.getByText(new RegExp(NOT_FOUND_LINE))).toBeVisible();
    expect(screen.queryByTestId("data-and-summary-page")).toBeNull();
    await waitFor(() => expect(detailReads()).toBeGreaterThan(0));
    expect(detailReads()).toBeLessThanOrEqual(2);
  });

  it("leaves a refused read to the new page, which shows the refusal", async () => {
    answer(httpError(403));
    openAt(`/dashboard/develop/experiment/${ID}/data`);
    const page = await screen.findByTestId("new-experiment-page");
    // The new page reads again when it mounts; the page stays mounted while
    // that read runs and after it is refused again.
    await waitFor(() => expect(detailReads()).toBe(2));
    await waitFor(() => expect(page).toHaveTextContent("refused"));
    expect(screen.getByTestId("new-experiment-page")).toBe(page);
    expect(screen.queryByTestId("experiment-page-loading")).toBeNull();
    expect(screen.queryByTestId("data-and-summary-page")).toBeNull();
  });

  it("decides again after a failed read: a later detail of an older experiment opens the Data and Summary page", async () => {
    // The page's own read and its two retries fail with a server error; the
    // new page reads again when it mounts, and that read answers.
    let reads = 0;
    requests.get.mockImplementation(async (url) => {
      if (url !== DETAIL_URL) throw new Error(`Unexpected GET: ${url}`);
      reads += 1;
      if (reads <= 3) throw httpError(503);
      return OLDER;
    });
    openAt(`/dashboard/develop/experiment/${ID}/data`);
    const page = await screen.findByTestId(
      "data-and-summary-page",
      {},
      { timeout: 15000 },
    );
    expect(page).toHaveTextContent("Support prompts, March");
    expect(screen.queryByTestId("new-experiment-page")).toBeNull();
    expect(screen.queryByTestId("experiment-page-loading")).toBeNull();
    expect(detailReads()).toBe(4);
  }, 25000);

  it("keeps a prototype build on the new page without asking which page first", async () => {
    mode.prototype = true;
    answer(OLDER);
    openAt(`/dashboard/develop/experiment/${ID}/data`);
    const page = await screen.findByTestId("new-experiment-page");
    expect(page).toBeVisible();
    expect(screen.queryByTestId("experiment-page-loading")).toBeNull();
    expect(screen.queryByTestId("data-and-summary-page")).toBeNull();
  });
});

describe("experiment page with unreleased parts hidden", () => {
  beforeEach(() => {
    unreleased.shown = false;
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: true,
    });
  });

  it.each([
    ["created before runs were recorded", OLDER],
    ["with runs", WITH_RUNS],
  ])(
    "opens an experiment %s on the Data and Summary page, as before",
    async (_label, response) => {
      answer(response);
      openAt(`/dashboard/develop/experiment/${ID}/data`);
      const page = await screen.findByTestId("data-and-summary-page");
      expect(page).toBeVisible();
      expect(screen.queryByTestId("experiment-page-loading")).toBeNull();
      expect(screen.queryByTestId("new-experiment-page")).toBeNull();
      // Only the page's own read: nothing is read to choose the page.
      await waitFor(() => expect(detailReads()).toBe(1));
    },
  );
});
