/* eslint-env node */
import React from "react";
import PropTypes from "prop-types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import Section from "src/sections/common/ux/oneview/Section";
import { useLayoutStore } from "src/sections/common/ux/oneview/layoutPrefs";
import StartFromExperiment, { START_FROM_PRESET } from "../StartFromExperiment";

const reads = vi.hoisted(() => ({
  runs: vi.fn(),
  detail: vi.fn(),
  preset: null,
}));

// Keep real query state, refetch and fetchQuery rejection semantics. Disabling
// retries models the terminal error after the production retry budget expires.
// W8: Start from reads the run list with useDatasetRuns (as the Experiments
// tab does); r2 stays mocked only for CarryOverToggle's import.
vi.mock("src/api/develop/runs", async () => {
  const { useQuery } = await import("@tanstack/react-query");
  return {
    useDatasetRuns: (datasetId) =>
      useQuery({
        queryKey: ["start-from-test-runs", datasetId],
        queryFn: () => reads.runs(),
        retry: false,
      }),
  };
});
vi.mock("src/api/develop/r2", () => ({ useRunConfigs: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: reads.detail },
  endpoints: {
    develop: {
      experiment: {
        getExperimentDetails: (id) => `/experiments/${id}/`,
      },
    },
  },
}));
vi.mock("../ExperimentFormContext", () => ({
  useExperimentForm: () => ({
    datasetId: "dataset-a",
    startFromPreset: reads.preset,
    versions: [{ id: "version-2", number: 2 }],
  }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../RunLetterBadge", () => ({ default: () => null }));
/* eslint-disable react/prop-types */
vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: ({ open, title, content, action, onClose }) =>
    open ? (
      <div role="dialog" aria-label={title}>
        <p>{content}</p>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        {action}
      </div>
    ) : null,
}));
/* eslint-enable react/prop-types */

const NETWORK_ERROR = { message: "Network Error", transportCode: "ERR_NETWORK" };
const SOURCE_RUNS = [
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
    target: {
      prompt_id: "source-prompt",
      prompt_version_id: "source-version",
      prompt_name: "Copied prompt",
      model: "test-model",
      model_params: { temperature: 0.2 },
    },
    config: {},
  },
];
const DETAIL = { data: { result: { runs: SOURCE_RUNS, trials: 3 } } };
const DRAFT = {
  name: "Keep my draft name",
  promptConfig: [
    {
      _itemId: "draft-card",
      targetType: "prompt",
      promptId: "draft-prompt",
      promptVersion: "draft-version",
      name: "Unsaved card",
    },
  ],
  startFrom: { experimentId: "previous-source", label: "#3", carryOver: false },
  trials: 7,
  userEvalMetrics: [{ id: "draft-eval" }],
  notCopied: 2,
  notCopiedLabels: ["#3Z"],
};

const FRESH_DRAFT = {
  name: "",
  experimentType: "llm",
  promptConfig: [],
  startFrom: null,
  trials: 1,
  userEvalMetrics: [],
  notCopied: 0,
  notCopiedLabels: [],
};
const LAYOUT_KEY = "fa.layout.proto-user.newExperiment";

let client;
let form;
let unhandled;

function Host({ initial = DRAFT, closedMoreSettings = false }) {
  form = useForm({ defaultValues: structuredClone(initial) });
  const startFrom = (
    <StartFromExperiment
      control={form.control}
      getValues={form.getValues}
      setValue={form.setValue}
    />
  );
  return (
    <QueryClientProvider client={client}>
      {closedMoreSettings ? (
        <Section
          surface="newExperiment"
          id="newexp.more"
          title="More settings"
          defaultOpen={false}
          keepMounted
        >
          {startFrom}
        </Section>
      ) : startFrom}
    </QueryClientProvider>
  );
}
Host.propTypes = {
  initial: PropTypes.object,
  closedMoreSettings: PropTypes.bool,
};

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const sourcePicker = () =>
  screen.getByRole("button", { name: "Start from experiment" });
const snapshot = () => structuredClone(form.getValues());

async function selectSource({ replace = true } = {}) {
  await waitFor(() => expect(sourcePicker()).toBeEnabled());
  fireEvent.click(sourcePicker());
  fireEvent.click(
    await screen.findByRole("menuitem", { name: /^#8 Source experiment/ }),
  );
  if (replace) {
    fireEvent.click(await screen.findByRole("button", { name: "Replace" }));
  }
}

async function expectCopied(initial = DRAFT) {
  await waitFor(() => {
    expect(form.getValues("startFrom")).toMatchObject({
      experimentId: "source-experiment",
      label: "#8",
      carryOver: false,
    });
    expect(sourcePicker()).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTestId("start-from")).toHaveAttribute("aria-busy", "false");
  });
  expect(form.getValues("promptConfig")).toEqual([
    expect.objectContaining({
      targetType: "prompt",
      promptId: "source-prompt",
      promptVersion: "source-version",
      name: "Copied prompt",
    }),
  ]);
  expect(form.getValues("trials")).toBe(3);
  expect(form.getValues("name")).toBe(initial.name);
  expect(form.getValues("userEvalMetrics")).toEqual(initial.userEvalMetrics);
  expect(form.getValues("notCopied")).toBe(0);
  expect(form.getValues("notCopiedLabels")).toEqual([]);
  expect(screen.queryByText("Copying experiment...")).not.toBeInTheDocument();
}

beforeEach(() => {
  window.localStorage.removeItem(LAYOUT_KEY);
  useLayoutStore.setState({ byKey: {} });
  reads.runs.mockReset().mockResolvedValue({ results: SOURCE_RUNS });
  reads.detail.mockReset().mockResolvedValue(DETAIL);
  reads.preset = null;
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  unhandled = vi.fn();
  window.addEventListener("unhandledrejection", unhandled);
  process.on("unhandledRejection", unhandled);
});

afterEach(async () => {
  cleanup();
  client.clear();
  window.localStorage.removeItem(LAYOUT_KEY);
  useLayoutStore.setState({ byKey: {} });
  // Observe both browser and Node promise events without swallowing failures.
  await new Promise((resolve) => setTimeout(resolve, 0));
  window.removeEventListener("unhandledrejection", unhandled);
  process.off("unhandledRejection", unhandled);
  expect(unhandled).not.toHaveBeenCalled();
});

describe("G2E-xp-wizard-02 Start from read recovery", () => {
  it("distinguishes a pending catalogue from a successful empty catalogue", async () => {
    const pending = deferred();
    reads.runs.mockReturnValueOnce(pending.promise);
    render(<Host />);
    const before = snapshot();

    expect(screen.getByRole("status")).toHaveTextContent("Loading experiments...");
    expect(sourcePicker()).toBeDisabled();
    expect(screen.queryByText(/No previous experiments/)).not.toBeInTheDocument();

    await act(async () => pending.resolve({ results: [] }));
    expect(await screen.findByText("No previous experiments to start from.")).toBeVisible();
    expect(screen.queryByText("Loading experiments...")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(sourcePicker()).toBeDisabled();
    expect(snapshot()).toEqual(before);
    expect(reads.detail).not.toHaveBeenCalled();
  });

  it.each(["menu", "one-shot"])(
    "recovers a response-less catalogue failure through explicit retry (%s)",
    async (entry) => {
      reads.preset = entry === "one-shot" ? START_FROM_PRESET : null;
      const pending = deferred();
      reads.runs
        .mockRejectedValueOnce(NETWORK_ERROR)
        .mockReturnValueOnce(pending.promise);
      render(<Host />);
      const before = snapshot();

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Could not load previous experiments",
      );
      expect(screen.queryByText(/No previous experiments/)).not.toBeInTheDocument();
      expect(snapshot()).toEqual(before);
      expect(reads.detail).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Retry experiments" }));
      await waitFor(() => expect(reads.runs).toHaveBeenCalledTimes(2));
      expect(sourcePicker()).toBeDisabled();
      expect(snapshot()).toEqual(before);

      await act(async () => pending.resolve({ results: SOURCE_RUNS }));
      if (entry === "menu") await selectSource();
      await expectCopied();
      expect(reads.detail).toHaveBeenCalledTimes(1);
      expect(reads.detail).toHaveBeenCalledWith("/experiments/source-experiment/");
    },
  );

  it.each(["empty draft", "replacement", "one-shot"])(
    "catches a failed detail read, preserves the draft and retries the same source (%s)",
    async (entry) => {
      reads.preset = entry === "one-shot" ? START_FROM_PRESET : null;
      const initial = entry === "empty draft"
        ? { ...DRAFT, promptConfig: [], startFrom: null }
        : DRAFT;
      const pending = deferred();
      reads.detail
        .mockRejectedValueOnce(NETWORK_ERROR)
        .mockReturnValueOnce(pending.promise);
      const view = render(<Host initial={initial} />);
      const before = snapshot();
      if (entry !== "one-shot") {
        await selectSource({ replace: entry === "replacement" });
      }

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Could not load the run cards for #8");
      expect(alert).toHaveTextContent("Your draft is unchanged");
      expect(snapshot()).toEqual(before);
      expect(sourcePicker()).toBeEnabled();
      expect(screen.getByTestId("start-from")).toHaveAttribute("aria-busy", "false");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(reads.detail).toHaveBeenCalledTimes(1);

      // A one-shot failure must wait for an explicit retry, not spin on render.
      view.rerender(<Host initial={initial} />);
      expect(reads.detail).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "Retry copy" }));
      await waitFor(() => expect(reads.detail).toHaveBeenCalledTimes(2));
      expect(sourcePicker()).toBeDisabled();
      expect(screen.getByRole("status")).toHaveTextContent("Copying experiment...");
      expect(snapshot()).toEqual(before);

      await act(async () => pending.resolve(DETAIL));
      await expectCopied();
      expect(reads.detail.mock.calls).toEqual([
        ["/experiments/source-experiment/"],
        ["/experiments/source-experiment/"],
      ]);
      view.rerender(<Host initial={initial} />);
      expect(reads.detail).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["catalogue", "detail"])(
    "G2E-xp-wizard-02-R1-%s reveals one-shot recovery in closed More settings and retries without changing the draft",
    async (read) => {
      reads.preset = START_FROM_PRESET;
      const failure = deferred();
      const retry = deferred();
      const failedRead = read === "catalogue" ? reads.runs : reads.detail;
      failedRead
        .mockReturnValueOnce(failure.promise)
        .mockReturnValueOnce(retry.promise);
      const view = render(<Host initial={FRESH_DRAFT} closedMoreSettings />);
      const before = snapshot();
      const moreSettings = screen.getByRole("button", { name: "More settings" });

      // Exercise the real keepMounted Collapse, with no saved open preference.
      expect(window.localStorage.getItem(LAYOUT_KEY)).toBeNull();
      expect(moreSettings).toHaveAttribute("aria-expanded", "false");
      expect(screen.getByTestId("start-from")).toBeInTheDocument();
      expect(screen.getByTestId("start-from")).not.toBeVisible();
      await waitFor(() => expect(failedRead).toHaveBeenCalledTimes(1));
      await act(async () => failure.reject(NETWORK_ERROR));

      const alert = await screen.findByRole("alert");
      expect(alert).toBeVisible();
      expect(alert).toHaveTextContent(
        read === "catalogue"
          ? "Could not load previous experiments"
          : "Could not load the run cards for #8",
      );
      expect(alert).toHaveTextContent("Your draft is unchanged");
      expect(moreSettings).toHaveAttribute("aria-expanded", "true");
      expect(JSON.parse(window.localStorage.getItem(LAYOUT_KEY)).sections).toEqual({
        "newexp.more": true,
      });
      expect(snapshot()).toEqual(before);
      if (read === "catalogue") expect(reads.detail).not.toHaveBeenCalled();

      // Revealing the controls must not repeat the failed read automatically.
      view.rerender(<Host initial={FRESH_DRAFT} closedMoreSettings />);
      expect(failedRead).toHaveBeenCalledTimes(1);
      const retryButton = screen.getByRole("button", {
        name: read === "catalogue" ? "Retry experiments" : "Retry copy",
      });
      expect(retryButton).toBeVisible();
      expect(retryButton).toBeEnabled();
      fireEvent.click(retryButton);
      await waitFor(() => expect(failedRead).toHaveBeenCalledTimes(2));
      expect(snapshot()).toEqual(before);
      expect(moreSettings).toHaveAttribute("aria-expanded", "true");

      await act(async () => retry.resolve(
        read === "catalogue" ? { results: SOURCE_RUNS } : DETAIL,
      ));
      await expectCopied(FRESH_DRAFT);
      expect(sourcePicker()).toBeVisible();
      expect(form.getValues("experimentType")).toBe("llm");
      expect(moreSettings).toHaveAttribute("aria-expanded", "true");
      expect(reads.runs).toHaveBeenCalledTimes(read === "catalogue" ? 2 : 1);
      expect(reads.detail).toHaveBeenCalledTimes(read === "detail" ? 2 : 1);
    },
  );

  it("shows a failed catalogue refresh without discarding cached choices or the draft", async () => {
    render(<Host />);
    await waitFor(() => expect(sourcePicker()).toBeEnabled());
    const before = snapshot();
    reads.runs.mockRejectedValueOnce(NETWORK_ERROR);
    await act(async () => {
      await client.refetchQueries({ queryKey: ["start-from-test-runs"] });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load previous experiments",
    );
    expect(sourcePicker()).toBeEnabled();
    expect(snapshot()).toEqual(before);
    fireEvent.click(screen.getByRole("button", { name: "Retry experiments" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(reads.runs).toHaveBeenCalledTimes(3);
    expect(snapshot()).toEqual(before);
    expect(reads.detail).not.toHaveBeenCalled();
  });
});
