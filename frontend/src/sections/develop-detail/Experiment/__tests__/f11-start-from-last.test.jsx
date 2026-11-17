// "Start from last experiment" (the ⌘K verb and ?newExperiment=bakeoff-v2)
// on the deployed launch build, with the run list as the backend sends it:
// no experiment number, no run letters, no target type, only task_kind.
// The one-shot copies the newest experiment's saved configs; when nothing
// can be copied it opens More settings and says so in a plain line; the
// palette verb names the experiment by name when it has no number.
// The one-shot runs once per wizard: More settings leaves and comes back
// (the first card switches the layout; the run picker opens and closes),
// and coming back never copies over the user's runs or reopens the section.
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
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import Section from "src/sections/common/ux/oneview/Section";
import { useLayoutStore } from "src/sections/common/ux/oneview/layoutPrefs";
import StartFromExperiment, {
  START_FROM_PRESET,
  copyableRun,
  startFromLastLabel,
} from "../StartFromExperiment";
import { moreSettingsChanges } from "../moreSettings";
import { experimentBodyOfValues } from "../experimentBody";

const NOT_IN_RELEASE = "Not available in this release";
const EMPTY_LINE = "No previous experiments to start from.";
const NOTHING_TO_COPY =
  "Started blank: no earlier experiment on this dataset has a run that can be copied. Add runs yourself.";
const LAYOUT_KEY = "fa.layout.proto-user.newExperiment";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  runs: [],
  preset: null,
  datasetId: "7f3c0a11-0000-4000-8000-0000000000f1",
}));
const RUNS_URL = `/model-hub/datasets/${h.datasetId}/runs/`;
const DETAIL_URL = (id) => `/model-hub/experiments/v2/${id}/`;

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: h.get } };
});
vi.mock("../ExperimentFormContext", () => ({
  useExperimentForm: () => ({
    datasetId: h.datasetId,
    startFromPreset: h.preset,
    versions: [{ id: "walk-version", number: 1 }],
  }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
/* eslint-disable react/prop-types */
vi.mock("../RunLetterBadge", () => ({
  // Same contract as the real badge: it reads letter.charCodeAt(0).
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

// One run as project_run sends it with include=config: the native kind
// ("execution"), the sanitized config (no kind), the run's own #n.
const backendRun = (n, experimentId, experimentName, extra = {}) => ({
  run_id: `run-${n}`,
  dataset_id: h.datasetId,
  experiment_id: experimentId,
  experiment_name: experimentName,
  experiment_description: null,
  dataset_version_id: "walk-version",
  dataset_version: { id: "walk-version", content_hash: "c0ffee" },
  ordinal: n,
  label: `#${n}`,
  name: `#${n}`,
  status: "completed",
  task_kind: "prompt",
  kind: "execution",
  source: "native",
  listed: true,
  final: true,
  trials: 1,
  progress: { status: "Completed", unit: "examples", total: 3, done: 3 },
  config: {
    model: { name: "liquid/lfm-3-1.7b", provider: "custom", params: {} },
  },
  created_at: `2026-11-11T0${n}:00:00Z`,
  ...extra,
});

// Newest first, as the backend orders the list (run ordinal, descending).
const WALK_RUNS = [
  backendRun(3, "walk-exp", "walk-exp-model-flows-1111"),
  backendRun(2, "walk-exp", "walk-exp-model-flows-1111"),
  backendRun(1, "first-exp", "first experiment"),
];

// Runs sent from the SDK: they ran outside Future AGI.
const EXTERNAL_RUNS = [
  backendRun(2, "sdk-exp", "From my code", {
    task_kind: "external",
    kind: "run",
    source: "external",
    config: null,
  }),
  backendRun(1, "sdk-exp", "From my code", {
    task_kind: "external",
    kind: "run",
    source: "external",
    config: null,
  }),
];

// GET experiments/v2/<id>/ as ExperimentDetailV2Serializer answers it:
// the saved prompt configs, and catalogue runs with no target type.
const DETAIL = {
  data: {
    status: true,
    result: {
      id: "walk-exp",
      name: "walk-exp-model-flows-1111",
      experiment_type: "llm",
      prompt_configs: [
        {
          id: "walk-config",
          name: "walk-capital",
          prompt_id: "walk-capital",
          prompt_name: "walk-capital",
          prompt_version: "walk-capital-v1",
          prompt_version_name: "v1",
          model: "liquid/lfm-3-1.7b",
          model_display_name: "liquid/lfm-3-1.7b",
          model_params: {},
          configuration: null,
          output_format: "string",
          order: 0,
        },
      ],
      agent_configs: [],
      http_configs: [],
      user_eval_metrics: [],
      runs: WALK_RUNS.slice(0, 2),
    },
  },
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

let form;

// The wizard keeps Start from inside the closed More settings section. The
// form belongs to the wizard; the section can leave and come back.
function Host({ client, shown = true }) {
  form = useForm({ defaultValues: structuredClone(FRESH_DRAFT) });
  return (
    <QueryClientProvider client={client}>
      {shown ? (
        <Section
          surface="newExperiment"
          id="newexp.more"
          title="More settings"
          defaultOpen={false}
          keepMounted
        >
          <StartFromExperiment
            control={form.control}
            setValue={form.setValue}
            getValues={form.getValues}
          />
        </Section>
      ) : null}
    </QueryClientProvider>
  );
}
Host.propTypes = {
  client: PropTypes.object.isRequired,
  shown: PropTypes.bool,
};

const startFromBox = () => screen.getByTestId("start-from");
const settled = () =>
  waitFor(() => expect(startFromBox()).toHaveAttribute("aria-busy", "false"));

const mount = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(<Host client={client} />);
  // More settings leaves the wizard and comes back (the run picker opened
  // and closed, or the first card switched the layout); the form stays.
  const remount = async () => {
    view.rerender(<Host client={client} shown={false} />);
    expect(screen.queryByTestId("start-from")).toBeNull();
    view.rerender(<Host client={client} shown />);
    await settled();
  };
  return { ...view, remount };
};

const moreSettings = () =>
  screen.getByRole("button", { name: "More settings" });
const detailCalls = () =>
  h.get.mock.calls
    .map(([url]) => url)
    .filter((url) => String(url).startsWith("/model-hub/experiments/v2/"));

beforeEach(() => {
  // The deployed build: a launch build (not the prototype, not a unit test).
  setLaunchOverrideForTest({ launch: true });
  window.localStorage.removeItem(LAYOUT_KEY);
  useLayoutStore.setState({ byKey: {} });
  h.runs = [];
  h.preset = null;
  h.get.mockReset().mockImplementation((url) => {
    if (url === RUNS_URL) return Promise.resolve(runsPage(h.runs));
    if (url === DETAIL_URL("walk-exp")) return Promise.resolve(DETAIL);
    return Promise.reject(new Error(`Unexpected GET ${url}`));
  });
});

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  window.localStorage.removeItem(LAYOUT_KEY);
  useLayoutStore.setState({ byKey: {} });
});

describe("Start from last experiment with the backend's run list", () => {
  it("copies the newest experiment's saved prompt config", async () => {
    h.preset = START_FROM_PRESET;
    h.runs = WALK_RUNS;
    mount();

    await waitFor(() =>
      expect(form.getValues("startFrom")).toMatchObject({
        experimentId: "walk-exp",
        label: "walk-exp-model-flows-1111",
        carryOver: false,
      }),
    );
    expect(form.getValues("promptConfig")).toEqual([
      expect.objectContaining({
        targetType: "prompt",
        promptId: "walk-capital",
        promptVersion: "walk-capital-v1",
        name: "walk-capital",
        model: [expect.objectContaining({ value: "liquid/lfm-3-1.7b" })],
      }),
    ]);
    expect(form.getValues("notCopied")).toBe(0);
    expect(detailCalls()).toEqual([DETAIL_URL("walk-exp")]);

    const startFrom = screen.getByTestId("start-from");
    await waitFor(() =>
      expect(startFrom.textContent).toContain(
        "walk-exp-model-flows-1111 (2 runs)",
      ),
    );
    expect(startFrom.textContent).not.toMatch(/undefined|NaN|#null/);
    // Start from sits in the closed section: read the DOM, not the a11y tree.
    expect(startFrom.querySelector('[role="alert"]')).toBeNull();
    expect(screen.queryByText(NOTHING_TO_COPY)).toBeNull();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
    // A copy that worked needs no recovery: More settings stays closed.
    expect(moreSettings()).toHaveAttribute("aria-expanded", "false");
  });

  it("opens More settings and says so when no earlier experiment has a run that can be copied", async () => {
    h.preset = START_FROM_PRESET;
    h.runs = EXTERNAL_RUNS;
    mount();
    const before = structuredClone(form.getValues());
    expect(moreSettings()).toHaveAttribute("aria-expanded", "false");

    await waitFor(() =>
      expect(moreSettings()).toHaveAttribute("aria-expanded", "true"),
    );
    const line = await screen.findByText(NOTHING_TO_COPY);
    await waitFor(() => expect(line).toBeVisible());
    expect(line.getAttribute("role")).toBe("status");
    expect(
      JSON.parse(window.localStorage.getItem(LAYOUT_KEY)).sections,
    ).toEqual({
      "newexp.more": true,
    });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(detailCalls()).toEqual([]);
    // The draft is unchanged; only the form-only one-shot marker is added.
    expect(structuredClone(form.getValues())).toEqual({
      ...before,
      startFromPresetDone: true,
    });
    expect(moreSettingsChanges(form.getValues())).toEqual([]);
    // The earlier experiments stay listed, so one can still be picked.
    expect(
      screen.getByRole("button", { name: "Start from experiment" }),
    ).toBeEnabled();
  });

  it("opens More settings with the calm empty line on a dataset with no experiments", async () => {
    h.preset = START_FROM_PRESET;
    h.runs = [];
    mount();

    await waitFor(() =>
      expect(moreSettings()).toHaveAttribute("aria-expanded", "true"),
    );
    const line = await screen.findByText(EMPTY_LINE);
    await waitFor(() => expect(line).toBeVisible());
    expect(screen.queryByText(NOTHING_TO_COPY)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(detailCalls()).toEqual([]);
  });

  it("without the one-shot, More settings stays as the user left it and no line shows", async () => {
    h.runs = EXTERNAL_RUNS;
    mount();

    // The list is read and its experiments offered (inside the closed section).
    await waitFor(() =>
      expect(screen.getByLabelText("Start from experiment")).toBeEnabled(),
    );
    expect(h.get).toHaveBeenCalledWith(RUNS_URL, expect.anything());
    expect(moreSettings()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(NOTHING_TO_COPY)).toBeNull();
    expect(detailCalls()).toEqual([]);
  });
});

describe("the one-shot runs once per wizard, not once per mount", () => {
  const waitForCopy = async () => {
    await waitFor(() =>
      expect(form.getValues("startFrom")).toMatchObject({
        experimentId: "walk-exp",
      }),
    );
    await settled();
    expect(detailCalls()).toEqual([DETAIL_URL("walk-exp")]);
  };

  it("More settings coming back does not copy over the runs the user changed", async () => {
    h.preset = START_FROM_PRESET;
    h.runs = WALK_RUNS;
    const { remount } = mount();
    await waitForCopy();
    // The marker is form-only: not in the create body, not in the summary.
    expect(
      JSON.stringify(
        experimentBodyOfValues(form.getValues(), { datasetId: h.datasetId }),
      ),
    ).not.toMatch(/PresetDone/);
    expect(moreSettingsChanges(form.getValues())).toEqual([
      "starting from walk-exp-model-flows-1111",
    ]);

    // The user edits the copied card, adds a run and asks for 3 trials.
    const [copied] = form.getValues("promptConfig");
    const mine = [
      { ...copied, name: "walk-capital (my edit)" },
      {
        _itemId: "added-by-user",
        experimentType: "llm",
        targetType: "prompt",
        promptId: "my-prompt",
        promptVersion: "my-prompt-v2",
        name: "my prompt",
        model: [{ id: "added-by-user", value: "liquid/lfm-3-1.7b" }],
        isDraft: false,
        variables: [],
        variableMapping: {},
      },
    ];
    act(() => {
      form.setValue("promptConfig", mine, { shouldDirty: true });
      form.setValue("trials", 3, { shouldDirty: true });
    });

    await remount();
    await remount();

    expect(detailCalls()).toEqual([DETAIL_URL("walk-exp")]);
    expect(form.getValues("promptConfig")).toEqual(mine);
    expect(form.getValues("trials")).toBe(3);
    expect(form.getValues("startFrom")).toMatchObject({
      experimentId: "walk-exp",
      label: "walk-exp-model-flows-1111",
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("after the user picks Blank, More settings coming back does not copy again", async () => {
    h.preset = START_FROM_PRESET;
    h.runs = WALK_RUNS;
    const { remount } = mount();
    await waitForCopy();

    // What Blank stores (apply(null)): no starting experiment, no cards.
    act(() => {
      form.setValue("startFrom", null, { shouldDirty: true });
      form.setValue("promptConfig", [], { shouldDirty: true });
      form.setValue("notCopied", 0);
      form.setValue("notCopiedLabels", []);
    });

    await remount();

    expect(detailCalls()).toEqual([DETAIL_URL("walk-exp")]);
    expect(form.getValues("startFrom")).toBeNull();
    expect(form.getValues("promptConfig")).toEqual([]);
    expect(startFromBox().textContent).toContain("Blank");
  });

  it("does not reopen More settings after the user collapsed it", async () => {
    h.preset = START_FROM_PRESET;
    h.runs = EXTERNAL_RUNS;
    const { remount } = mount();
    await waitFor(() =>
      expect(moreSettings()).toHaveAttribute("aria-expanded", "true"),
    );
    await settled();

    fireEvent.click(moreSettings());
    expect(moreSettings()).toHaveAttribute("aria-expanded", "false");

    await remount();

    expect(moreSettings()).toHaveAttribute("aria-expanded", "false");
    expect(
      JSON.parse(window.localStorage.getItem(LAYOUT_KEY)).sections,
    ).toEqual({
      "newexp.more": false,
    });
    // The plain line still says why the wizard started blank.
    expect(screen.getByTestId("start-from-nothing-to-copy")).toHaveTextContent(
      NOTHING_TO_COPY,
    );
    expect(detailCalls()).toEqual([]);
  });
});

describe("what the one-shot copies and how the palette names it", () => {
  it("a prompt or agent graph run from the run list is copyable; an external run is not", () => {
    expect(copyableRun(WALK_RUNS[0])).toBe(true);
    expect(copyableRun({ ...WALK_RUNS[0], task_kind: "graph" })).toBe(true);
    expect(copyableRun(EXTERNAL_RUNS[0])).toBe(false);
    // The prototype shapes keep their answers.
    expect(copyableRun({ target_type: "prompt" })).toBe(true);
    expect(copyableRun({ config: { kind: "http" } })).toBe(true);
    expect(copyableRun({ config: { kind: "harness" } })).toBe(false);
    expect(copyableRun({ config: { kind: "code" } })).toBe(false);
  });

  it("names the last experiment by number, else by name, never '#undefined'", () => {
    expect(
      startFromLastLabel({
        experiment_ordinal: 4,
        experiment_name: "Bake-off",
      }),
    ).toBe("Start from last experiment (#4)");
    expect(startFromLastLabel(WALK_RUNS[0])).toBe(
      "Start from last experiment (walk-exp-model-flows-1111)",
    );
    expect(startFromLastLabel({ experiment_name: null })).toBe(
      "Start from last experiment",
    );
    expect(startFromLastLabel(null)).toBe("Start from last experiment");
    [WALK_RUNS[0], { experiment_name: null }, null].forEach((run) =>
      expect(startFromLastLabel(run)).not.toMatch(/undefined|null|#\)/),
    );
  });
});
