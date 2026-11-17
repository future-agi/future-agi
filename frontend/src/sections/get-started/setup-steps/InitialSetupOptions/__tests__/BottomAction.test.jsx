/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router-dom";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  act,
  renderWithRouter,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  enqueueSnackbar: vi.fn(),
  experimentsTabShown: vi.fn(),
}));

// Mock only the axios default instance; keep the real `endpoints` so the URLs
// are the genuine ones.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: mocks.get } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.enqueueSnackbar }));
// The launch switch decides whether a dataset's Experiments tab shows; every
// other answer stays the real one.
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    isDatasetTabShown: (tab) =>
      tab === "experiments"
        ? mocks.experimentsTabShown()
        : actual.isDatasetTabShown(tab),
  };
});

const { endpoints } = await import("src/utils/axios");
const { handleError } = await import("src/utils/queryErrorHandler");
const { setRealBackendForTests } = await import(
  "src/sections/develop/realBackendCaps"
);
const { default: BottomAction } = await import("../BottomAction");

// Without a dataset, the step above the bar already says how to start an
// experiment, so the bar adds no second instruction next to "Go to datasets".
const EXPERIMENT_HINT = /experiment/i;

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="location">{`${pathname}${search}`}</div>;
}

// The app's wiring (src/app.jsx): a failed query also reaches handleError.
function renderStep(currentLabel) {
  const queryClient = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <BottomAction currentLabel={currentLabel} setCurrentLabel={vi.fn()} />
      <LocationProbe />
    </QueryClientProvider>,
    { route: "/dashboard/get-started" },
  );
}

// Lets a dataset lookup, if the screen makes one, answer before asserting.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });

const listAnswer = (datasets) => ({
  data: {
    result: {
      datasets,
      total_count: datasets.length,
      total_pages: datasets.length ? 1 : 0,
    },
  },
});

const sortParam = (params) => JSON.parse(params.sort);

describe("Get started bottom action", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue(listAnswer([]));
    mocks.experimentsTabShown.mockReturnValue(true);
  });

  afterEach(() => setRealBackendForTests(null));

  describe("Run your first experiment, when the user has a dataset", () => {
    it("opens the newest dataset's Experiments tab and says so", async () => {
      mocks.get.mockResolvedValue(
        listAnswer([{ id: "ds-1", name: "Support tickets" }]),
      );
      const user = userEvent.setup();
      renderStep("RunFirstExperiment");

      const button = await screen.findByRole("button", {
        name: "Go to Experiments",
      });
      expect(
        screen.getByText(
          "Opens the Experiments tab of your newest dataset, Support tickets.",
        ),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Go to Datasets" }),
      ).not.toBeInTheDocument();

      await user.click(button);

      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/develop/ds-1?tab=experiments",
      );
    });

    it("on the real backend, reads get-datasets newest first and opens that dataset", async () => {
      setRealBackendForTests(true);
      mocks.get.mockResolvedValue(
        listAnswer([{ id: "ds-real", name: "Golden set" }]),
      );
      const user = userEvent.setup();
      renderStep("RunFirstExperiment");

      await user.click(
        await screen.findByRole("button", { name: "Go to Experiments" }),
      );

      expect(mocks.get).toHaveBeenCalledTimes(1);
      const [url, { params }] = mocks.get.mock.calls[0];
      expect(url).toBe(endpoints.develop.getDatasets());
      expect(params.page_size).toBe(1);
      expect(sortParam(params)).toEqual([
        { column_id: "created_at", type: "descending" },
      ]);
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/develop/ds-real?tab=experiments",
      );
    });

    it("asks for one dataset, newest first and not starred first", async () => {
      renderStep("RunFirstExperiment");

      await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
      const [url, { params }] = mocks.get.mock.calls[0];
      expect(url).toBe(endpoints.develop.w9.datasetsQuery);
      expect(params.page_size).toBe(1);
      expect(params.starred_first).toBe("false");
      expect(sortParam(params)).toEqual([
        { column_id: "created_at", type: "descending" },
      ]);
    });
  });

  describe("Run your first experiment, falling back to the Datasets list", () => {
    async function expectDatasetsFallback(user) {
      expect(screen.queryByText(EXPERIMENT_HINT)).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Go to Experiments" }),
      ).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Go to Datasets" }));

      expect(screen.getByTestId("location").textContent).toBe(
        "/dashboard/develop",
      );
    }

    it("with no datasets: names the Datasets page it opens, with no second instruction", async () => {
      const user = userEvent.setup();
      renderStep("RunFirstExperiment");
      await settle();

      await expectDatasetsFallback(user);
    });

    it("while the list is loading", async () => {
      mocks.get.mockReturnValue(new Promise(() => {}));
      const user = userEvent.setup();
      renderStep("RunFirstExperiment");
      await settle();

      await expectDatasetsFallback(user);
    });

    it("when the list fails, without an error message for the lookup", async () => {
      mocks.get.mockRejectedValue({
        result: "Something broke.",
        statusCode: 400,
      });
      const user = userEvent.setup();
      renderStep("RunFirstExperiment");
      await settle();

      await expectDatasetsFallback(user);
      expect(mocks.enqueueSnackbar).not.toHaveBeenCalled();
    });

    it("when this build hides a dataset's Experiments tab, without looking up a dataset", async () => {
      mocks.experimentsTabShown.mockReturnValue(false);
      mocks.get.mockResolvedValue(
        listAnswer([{ id: "ds-1", name: "Support tickets" }]),
      );
      const user = userEvent.setup();
      renderStep("RunFirstExperiment");
      await settle();

      expect(mocks.get).not.toHaveBeenCalled();
      await expectDatasetsFallback(user);
    });
  });

  it("Set up observability keeps its button, shows no experiment hint and looks up no datasets", async () => {
    const user = userEvent.setup();
    renderStep("SetupObsabilityInApplication");
    await settle();

    expect(screen.queryByText(EXPERIMENT_HINT)).not.toBeInTheDocument();
    expect(mocks.get).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Go to Tracing" }));

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/observe",
    );
  });

  it("Add keys looks up no datasets", async () => {
    renderStep("addKeys");
    await settle();

    expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument();
    expect(mocks.get).not.toHaveBeenCalled();
  });
});
