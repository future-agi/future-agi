// W8: on a fresh dataset with no
// experiments, New experiment › More settings › Start from said "Could not
// load previous experiments", and Retry sent no request. The deployed build
// is a launch build that does not install the absent-path guard
// (SHOW_UNRELEASED_PARTS), and the Experiments tab reads the same run list
// there. Start from must read it too: empty reads as a calm line, a failure
// says so and Retry sends the run list read again, experiments are listed.
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { moreSettingsChanges } from "../moreSettings";
import StartFromExperiment from "../StartFromExperiment";

const NOT_IN_RELEASE = "Not available in this release";
const EMPTY_LINE = "No previous experiments to start from.";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  runs: [],
  datasetId: "205e281b-0000-4000-8000-0000000000aa",
}));
const RUNS_URL = `/model-hub/datasets/${h.datasetId}/runs/`;

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: h.get } };
});
vi.mock("../ExperimentFormContext", () => ({
  useExperimentForm: () => ({
    datasetId: h.datasetId,
    startFromPreset: null,
    versions: [{ id: "version-1", number: 1 }],
  }),
}));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  useLayoutPrefs: () => [{}, () => {}],
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
/* eslint-disable react/prop-types */
vi.mock("../RunLetterBadge", () => ({
  // Same contract as the real badge: it reads letter.charCodeAt(0), so a
  // run without a letter throws there.
  default: function RunLetterBadge({ letter }) {
    return <span>{letter.charCodeAt(0) ? letter : null}</span>;
  },
}));
/* eslint-enable react/prop-types */

// GET datasets/<id>/runs/ as results_catalog.list_results_runs answers it.
const runsPage = (results) => ({
  data: {
    status: true,
    result: {
      count: results.length,
      results,
      baseline: {},
      pinned_run_id: null,
      baseline_run_id: null,
      metric_defs: [],
      limit: 500,
      offset: 0,
      has_more: false,
      next: null,
      scope: { population: results.length, complete: true },
    },
  },
});

// The shape the wizard tests use (experiment number, letters, rows).
const PROTOTYPE_RUNS = [
  {
    run_id: "source-run",
    experiment_id: "source-experiment",
    experiment_ordinal: 8,
    experiment_name: "Source experiment",
    dataset_version: { number: 1 },
    letter: "A",
    label: "#8A",
    examples: 12,
    trials: 3,
    target_type: "prompt",
    config: {},
  },
];

// What results_catalog.project_run sends today: each run's own #n, no
// experiment number, no letter, no row count (progress.total instead).
const realRun = (n) => ({
  run_id: `walk-run-${n}`,
  dataset_id: h.datasetId,
  experiment_id: "walk-exp",
  experiment_name: "walk-exp-model-flows-1111",
  dataset_version_id: "walk-version",
  dataset_version: { id: "walk-version", content_hash: "c0ffee" },
  ordinal: n,
  label: `#${n}`,
  status: "completed",
  task_kind: "prompt",
  kind: "run",
  listed: true,
  final: true,
  trials: 1,
  progress: { status: "Completed", unit: "examples", total: 3, done: 3 },
  config: {},
});
const REAL_RUNS = [realRun(3), realRun(2), realRun(1)];

const DETAIL = {
  data: {
    result: {
      trials: 1,
      runs: [
        {
          run_id: "walk-run-1",
          target_type: "prompt",
          target: {
            prompt_id: "walk-capital",
            prompt_version_id: "walk-capital-v1",
            prompt_name: "walk-capital",
            model: "liquid/lfm-3-1.7b",
            model_params: {},
          },
          config: {},
        },
      ],
    },
  },
};

let form;

function Host() {
  form = useForm({
    defaultValues: {
      startFrom: null,
      promptConfig: [],
      trials: 1,
      userEvalMetrics: [],
      notCopied: 0,
      notCopiedLabels: [],
    },
  });
  return (
    <StartFromExperiment
      control={form.control}
      setValue={form.setValue}
      getValues={form.getValues}
    />
  );
}

const mount = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Host />
    </QueryClientProvider>,
  );
};

const picker = () =>
  screen.getByRole("button", { name: "Start from experiment" });

beforeEach(() => {
  // The deployed build: a launch build (not the prototype, not a unit test).
  setLaunchOverrideForTest({ launch: true });
  h.runs = [];
  h.get.mockReset().mockImplementation((url) => {
    if (url === RUNS_URL) return Promise.resolve(runsPage(h.runs));
    if (String(url).startsWith("/model-hub/experiments/v2/"))
      return Promise.resolve(DETAIL);
    return Promise.reject(new Error(`Unexpected GET ${url}`));
  });
});

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("W8 Start from on the deployed launch build", () => {
  it("a dataset with no experiments shows Blank and a calm line, with no error or retry", async () => {
    mount();

    expect(await screen.findByText(EMPTY_LINE)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      screen.queryByText(/Could not load previous experiments/),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Retry experiments" }),
    ).toBeNull();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
    expect(picker().textContent).toContain("Blank");
    expect(picker()).toHaveProperty("disabled", true);
    expect(h.get).toHaveBeenCalledWith(
      RUNS_URL,
      expect.objectContaining({
        params: expect.objectContaining({ include: "config", offset: 0 }),
      }),
    );
  });

  it("a failed read says so in plain words, and Retry sends the run list read again", async () => {
    h.get.mockImplementationOnce(() =>
      Promise.reject({
        statusCode: 404,
        message: "Native run provenance is unavailable",
      }),
    );
    mount();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(
      "Could not load previous experiments. Your draft is unchanged.",
    );
    expect(screen.queryByText(/Native run provenance/)).toBeNull();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry experiments" }));
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get.mock.calls.map(([url]) => url)).toEqual([RUNS_URL, RUNS_URL]);
    expect(await screen.findByText(EMPTY_LINE)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a dataset with experiments still lists them", async () => {
    h.runs = PROTOTYPE_RUNS;
    mount();

    await waitFor(() => expect(picker()).toHaveProperty("disabled", false));
    fireEvent.click(picker());
    const item = await screen.findByRole("menuitem", {
      name: /^#8 Source experiment \(1 run\)/,
    });
    expect(item.textContent).toContain("v1 · 12 rows · 3 trials · #8A");
    expect(screen.queryByText(EMPTY_LINE)).toBeNull();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
  });

  it("lists and copies an experiment as the backend's run list sends it (no experiment number or letters)", async () => {
    h.runs = REAL_RUNS;
    mount();

    await waitFor(() => expect(picker()).toHaveProperty("disabled", false));
    fireEvent.click(picker());
    const item = await screen.findByRole("menuitem", {
      name: /^walk-exp-model-flows-1111 \(3 runs\)/,
    });
    expect(item.textContent).toContain("3 rows · 1 trial · #1 #2 #3");
    expect(item.textContent).not.toMatch(/undefined|NaN|#null/);

    fireEvent.click(item);
    await waitFor(() =>
      expect(form.getValues("startFrom")).toMatchObject({
        experimentId: "walk-exp",
        label: "walk-exp-model-flows-1111",
      }),
    );
    await waitFor(() =>
      expect(picker().textContent).toContain(
        "walk-exp-model-flows-1111 (3 runs)",
      ),
    );
    expect(screen.getByTestId("start-from").textContent).not.toMatch(
      /undefined|NaN/,
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(moreSettingsChanges(form.getValues())).toEqual([
      "starting from walk-exp-model-flows-1111",
    ]);
    expect(h.get).toHaveBeenCalledWith("/model-hub/experiments/v2/walk-exp/");
  });
});
