import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import axios from "src/utils/axios";
import SamplingPolicyEditor from "./SamplingPolicyEditor";
import { defaultSamplingPolicy } from "./samplingPolicyForm";
import { NewTaskValidationSchema, getNewTaskFilters } from "../schema";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    project: { samplingPreview: () => "/tracer/eval-task/sampling_preview/" },
  },
}));

function Harness({ policy = null, evaluators = [] }) {
  const { control, getValues } = useForm({
    defaultValues: {
      samplingPolicy: policy,
      evalsDetails: evaluators,
      project: "project-id",
      rowType: "spans",
      runType: "continuous",
      filters: [],
    },
  });
  return <SamplingPolicyEditor control={control} getValues={getValues} />;
}

Harness.propTypes = { policy: PropTypes.object, evaluators: PropTypes.array };

const WINDOW = {
  startDate: "2026-10-01T00:00:00.000Z",
  endDate: "2026-10-02T00:00:00.000Z",
};

function HistoricalHarness({ policy }) {
  const { control, getValues } = useForm({
    defaultValues: {
      samplingPolicy: policy,
      evalsDetails: [],
      project: "project-id",
      rowType: "spans",
      runType: "historical",
      datePreset: "Custom",
      filters: [],
      ...WINDOW,
    },
  });
  return <SamplingPolicyEditor control={control} getValues={getValues} />;
}

HistoricalHarness.propTypes = { policy: PropTypes.object };

// The real task form validator gates the save button, as on the task pages.
function SubmitHarness({ policy, onSubmit }) {
  const { control, getValues, handleSubmit } = useForm({
    defaultValues: {
      name: "cohort",
      project: "project-id",
      spansLimit: "",
      samplingRate: 100,
      samplingPolicy: policy,
      evalsDetails: [{ id: "eval" }],
      rowType: "spans",
      runType: "continuous",
      filters: [],
      ...WINDOW,
    },
    resolver: zodResolver(NewTaskValidationSchema()),
  });
  return (
    <>
      <SamplingPolicyEditor control={control} getValues={getValues} />
      <button type="button" onClick={handleSubmit(onSubmit)}>
        Save task
      </button>
    </>
  );
}

SubmitHarness.propTypes = {
  policy: PropTypes.object,
  onSubmit: PropTypes.func.isRequired,
};

const SELECTION = /^Selection(?: |$)/;
const MODES = [
  {
    mode: "rate",
    label: "Rate (%)",
    rule: { rate: 10 },
    error: "Enter a rate from 0 to 100.",
    typed: "25",
    expected: { rate: 25 },
  },
  {
    mode: "target_n",
    label: "Target units",
    rule: { target_n: 2 },
    error: "Enter the number of target units.",
    typed: "3",
    expected: { target_n: 3 },
  },
];

beforeEach(() => vi.clearAllMocks());

describe("Sampling policy editor", () => {
  it("leaves legacy tasks without a policy until enabled", async () => {
    render(<Harness />);
    expect(
      screen.queryByRole("button", { name: "Preview sampling policy" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Contact-aware sampling policy" }),
    );
    expect(
      screen.getByRole("combobox", { name: "Sampling unit" }),
    ).toHaveTextContent("session");
  });

  it("selects and previews a closed contact quota for a continuous task", async () => {
    axios.post.mockResolvedValue({
      data: {
        result: {
          strata: [],
          population_note: "Closed quota",
          expected_evaluations: null,
          cost_note: "Pricing unavailable",
        },
      },
    });
    render(<Harness />);
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Contact-aware sampling policy" }),
    );
    await userEvent.click(
      screen.getByRole("combobox", { name: /^Selection(?: |$)/ }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Target units" }));
    const target = screen.getByRole("spinbutton", { name: "Target units" });
    await userEvent.clear(target);
    expect(
      screen.getByRole("combobox", { name: /^Selection(?: |$)/ }),
    ).toHaveTextContent("Target units");
    expect(
      screen.getByRole("spinbutton", { name: "Target units" }),
    ).toHaveValue(null);
    await userEvent.type(target, "1");
    expect(target).toHaveValue(1);
    const minimum = screen.getByRole("spinbutton", { name: "Minimum units" });
    await userEvent.type(minimum, "2");
    await userEvent.clear(minimum);
    expect(
      screen.getByRole("combobox", { name: /^Population mode(?: |$)/ }),
    ).toHaveTextContent("Closed window · exact quotas");
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sampling policy" }),
    );
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        "/tracer/eval-task/sampling_preview/",
        expect.objectContaining({
          sampling_policy: expect.objectContaining({
            sampling_unit: "session",
            mode: "closed",
            default: { target_n: 1 },
          }),
        }),
      ),
    );
  });

  it.each(MODES)(
    "keeps $mode mode with an explicit blank, blocks preview, then previews the retyped value",
    async ({ label, rule, error, typed, expected }) => {
      axios.post.mockResolvedValue({
        data: { result: { strata: [], population_note: "", cost_note: "" } },
      });
      const policy = defaultSamplingPolicy();
      policy.default = { ...rule, min_n: 1 };
      render(<Harness policy={policy} />);
      const input = screen.getByRole("spinbutton", { name: label });
      await userEvent.clear(input);
      expect(
        screen.getByRole("combobox", { name: SELECTION }),
      ).toHaveTextContent(label);
      expect(screen.getByRole("spinbutton", { name: label })).toHaveValue(null);
      expect(screen.getByText(error)).toBeVisible();
      expect(
        screen.getByRole("button", { name: "Preview sampling policy" }),
      ).toBeDisabled();
      expect(axios.post).not.toHaveBeenCalled();
      // Clearing an optional floor removes it rather than sending a blank.
      await userEvent.clear(
        screen.getByRole("spinbutton", { name: "Minimum units" }),
      );
      await userEvent.type(input, typed);
      expect(screen.queryByText(error)).not.toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", { name: "Preview sampling policy" }),
      );
      await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
      expect(axios.post.mock.calls[0][1].sampling_policy.default).toEqual(
        expected,
      );
    },
  );

  it.each(MODES)(
    "never saves a cleared $mode quota as another mode, zero or 100%",
    async ({ label, rule, error, typed, expected }) => {
      const onSubmit = vi.fn();
      const policy = defaultSamplingPolicy();
      policy.default = rule;
      render(<SubmitHarness policy={policy} onSubmit={onSubmit} />);
      await userEvent.clear(screen.getByRole("spinbutton", { name: label }));
      await userEvent.click(screen.getByRole("button", { name: "Save task" }));
      expect(onSubmit).not.toHaveBeenCalled();
      expect(screen.getByText(error)).toBeVisible();
      expect(
        screen.getByRole("combobox", { name: SELECTION }),
      ).toHaveTextContent(label);
      await userEvent.type(
        screen.getByRole("spinbutton", { name: label }),
        typed,
      );
      await userEvent.click(screen.getByRole("button", { name: "Save task" }));
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(onSubmit.mock.calls[0][0].samplingPolicy.default).toEqual(
        expected,
      );
    },
  );

  it.each([
    [{ rate: "" }, "default.rate"],
    [{ target_n: "" }, "default.target_n"],
    [{}, "default.rate"],
    [{ rate: Number.NaN }, "default.rate"],
    [{ target_n: 1.5 }, "default.target_n"],
    [{ rate: 10, max_n: "" }, "default.max_n"],
  ])("rejects the invalid quota %j at the save boundary", (rule, path) => {
    const policy = defaultSamplingPolicy();
    policy.default = rule;
    const result = NewTaskValidationSchema().safeParse({
      name: "cohort",
      project: "project",
      spansLimit: "",
      samplingRate: 50,
      samplingPolicy: policy,
      evalsDetails: [{ id: "eval" }],
      runType: "continuous",
      rowType: "spans",
      filters: [],
      ...WINDOW,
    });
    expect(result.success).toBe(false);
    expect(result.error.issues.map((issue) => issue.path.join("."))).toContain(
      `samplingPolicy.${path}`,
    );
  });

  it.each([
    ["0.", "5", 0.5],
    ["-", "5", -5],
    ["1e-", "2", 0.01],
  ])(
    "preserves the numeric predicate draft %s and gates preview and save until complete",
    async (prefix, suffix, expected) => {
      axios.post.mockResolvedValue({
        data: { result: { strata: [], population_note: "", cost_note: "" } },
      });
      const policy = defaultSamplingPolicy();
      policy.strata = [
        {
          name: "costly",
          rate: 50,
          filters: [{ source: "span", key: "cost", op: "gt", values: [12] }],
        },
      ];
      const onSubmit = vi.fn();
      render(<SubmitHarness policy={policy} onSubmit={onSubmit} />);
      const input = screen.getByRole("textbox", {
        name: "Values (comma separated)",
      });
      const preview = screen.getByRole("button", {
        name: "Preview sampling policy",
      });
      const save = screen.getByRole("button", { name: "Save task" });
      await userEvent.clear(input);
      expect(input).toHaveValue("");
      expect(preview).toBeDisabled();
      await userEvent.click(save);
      expect(onSubmit).not.toHaveBeenCalled();
      await userEvent.type(input, prefix);
      expect(input).toHaveValue(prefix);
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(preview).toBeDisabled();
      await userEvent.click(save);
      expect(onSubmit).not.toHaveBeenCalled();
      expect(axios.post).not.toHaveBeenCalled();
      await userEvent.type(input, suffix);
      expect(input).toHaveValue(prefix + suffix);
      expect(preview).toBeEnabled();
      await userEvent.click(preview);
      await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
      expect(
        axios.post.mock.calls[0][1].sampling_policy.strata[0].filters[0].values,
      ).toEqual([expected]);
      // Preview/save serialization must not rewrite the in-progress form text.
      expect(input).toHaveValue(prefix + suffix);
      await userEvent.click(save);
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(
        onSubmit.mock.calls[0][0].samplingPolicy.strata[0].filters[0].values,
      ).toEqual([expected]);
    },
  );

  it.each([
    [],
    [""],
    [" "],
    ["-"],
    ["0."],
    ["1e"],
    ["1e-"],
    ["Infinity"],
    ["1e999"],
    [Number.NaN],
    [Infinity],
    [null],
    [true],
    [1, 2],
  ])(
    "rejects incomplete or nonfinite numeric predicate values %j at save",
    (...values) => {
      const policy = defaultSamplingPolicy();
      policy.strata = [
        {
          name: "costly",
          rate: 50,
          filters: [{ source: "span", key: "cost", op: "gt", values }],
        },
      ];
      const result = NewTaskValidationSchema().safeParse({
        name: "cohort",
        project: "project",
        spansLimit: "",
        samplingRate: 50,
        samplingPolicy: policy,
        evalsDetails: [{ id: "eval" }],
        runType: "continuous",
        rowType: "spans",
        filters: [],
        ...WINDOW,
      });
      expect(result.success).toBe(false);
      expect(
        result.error.issues.map((issue) => issue.path.join(".")),
      ).toContain("samplingPolicy.strata.0.filters.0.values");
    },
  );

  it("previews a historical policy without a second hidden task date range", async () => {
    axios.post.mockResolvedValue({
      data: { result: { strata: [], population_note: "", cost_note: "" } },
    });
    const policy = defaultSamplingPolicy();
    render(<HistoricalHarness policy={policy} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sampling policy" }),
    );
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    const { filters, sampling_policy: sent } = axios.post.mock.calls[0][1];
    expect(filters).not.toHaveProperty("date_range");
    expect(filters).not.toHaveProperty("date_preset");
    expect(sent.window).toEqual(policy.window);
    const legacy = getNewTaskFilters(
      { runType: "historical", datePreset: "Custom", ...WINDOW },
      "project-id",
    ).filters;
    expect(legacy.date_range).toEqual([WINDOW.startDate, WINDOW.endDate]);
  });

  it("previews the saved policy and shows returned population, not invented cost", async () => {
    const policy = defaultSamplingPolicy();
    policy.default = { target_n: 2, min_n: 1, max_n: 3, rare_below: 2 };
    axios.post.mockResolvedValue({
      data: {
        status: true,
        result: {
          strata: [
            {
              stratum: "rare",
              eligible_units: 3,
              expected_units: 2,
              selected_rows: 7,
            },
          ],
          population_note: "Frozen when executed",
          cost_note: "Pricing unavailable",
          estimated_cost: null,
          evaluator_count: 2,
          expected_evaluations: 14,
          selected_evaluations: 14,
        },
      },
    });
    render(
      <Harness
        policy={policy}
        evaluators={[{ id: "eval-a" }, { id: "eval-b" }]}
      />,
    );
    expect(
      screen.getByRole("spinbutton", { name: "Target units" }),
    ).toHaveValue(2);
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sampling policy" }),
    );
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        "/tracer/eval-task/sampling_preview/",
        expect.objectContaining({
          project: "project-id",
          row_type: "spans",
          sampling_policy: policy,
          evals: ["eval-a", "eval-b"],
        }),
      ),
    );
    expect(await screen.findByRole("cell", { name: "rare" })).toBeVisible();
    expect(screen.getByRole("cell", { name: "7" })).toBeVisible();
    expect(screen.getByText("Pricing unavailable")).toBeVisible();
    expect(
      screen.getByText("Expected evaluation runs: 14 across 2 evaluator(s)."),
    ).toBeVisible();
  });

  it("removes quota controls when switching to live percentage mode", async () => {
    const policy = defaultSamplingPolicy();
    policy.default = { target_n: 5, min_n: 2, max_n: 8, rare_below: 4 };
    render(<Harness policy={policy} />);
    await userEvent.click(
      screen.getByRole("combobox", { name: "Population mode" }),
    );
    await userEvent.click(
      screen.getByRole("option", { name: "Live window · percentage only" }),
    );
    expect(
      screen.queryByRole("spinbutton", { name: "Target units" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("spinbutton", { name: "Minimum units" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Rate (%)" })).toHaveValue(
      10,
    );
  });

  it("lets users build ordered slices with their own filters and rates", async () => {
    render(<Harness policy={defaultSamplingPolicy()} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Add priority slice" }),
    );
    expect(screen.getByText("Priority 1")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Add slice predicate" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Slice attribute" }),
      "locale",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Values (comma separated)" }),
      "en,fr",
    );
    expect(
      screen.getByRole("textbox", { name: "Values (comma separated)" }),
    ).toHaveValue("en,fr");
  });

  it("surfaces failed previews without displaying a stale successful estimate", async () => {
    axios.post.mockRejectedValue(new Error("offline"));
    render(<Harness policy={defaultSamplingPolicy()} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sampling policy" }),
    );
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("preserves the policy through the shared task form transform", () => {
    const policy = defaultSamplingPolicy();
    const result = NewTaskValidationSchema().parse({
      name: "cohort",
      project: "project",
      spansLimit: 100,
      samplingRate: 50,
      samplingPolicy: policy,
      evalsDetails: [{ id: "eval" }],
      startDate: policy.window.start,
      endDate: policy.window.end,
      runType: "historical",
      rowType: "spans",
      filters: [],
    });
    expect(result.samplingPolicy).toEqual(policy);
    expect(result.filters).not.toHaveProperty("date_range");
    expect(result.filters).not.toHaveProperty("date_preset");
  });

  it("keeps independent policies key-free and sends explicit project coordination", async () => {
    axios.post.mockResolvedValue({
      data: { result: { strata: [], population_note: "", cost_note: "" } },
    });
    const policy = defaultSamplingPolicy();
    policy.default = { target_n: 2 };
    expect(policy).not.toHaveProperty("coordination");
    render(<Harness policy={policy} />);
    const coordination = () =>
      screen.getByRole("combobox", { name: /^Coordination(?: |$)/ });
    expect(coordination()).toHaveTextContent("Independent");
    expect(
      screen.queryByRole("spinbutton", { name: "PRN start (%)" }),
    ).not.toBeInTheDocument();
    await userEvent.click(coordination());
    await userEvent.click(
      screen.getByRole("option", {
        name: "Coordinated · nested across this project",
      }),
    );
    // The flow locators stay unique: one Selection control, one Target units.
    expect(screen.getAllByRole("combobox", { name: SELECTION })).toHaveLength(
      1,
    );
    expect(
      screen.getAllByRole("spinbutton", { name: "Target units" }),
    ).toHaveLength(1);
    await userEvent.type(
      screen.getByRole("textbox", { name: "PRN panel" }),
      "q4",
    );
    const start = screen.getByRole("spinbutton", { name: "PRN start (%)" });
    await userEvent.type(start, "100");
    expect(screen.getByText(/up to \(not including\) 100/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Preview sampling policy" }),
    ).toBeDisabled();
    await userEvent.clear(start);
    await userEvent.type(start, "12.5");
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sampling policy" }),
    );
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post.mock.calls[0][1].sampling_policy).toMatchObject({
      coordination: "project",
      panel: "q4",
      prn_start: 12.5,
      default: { target_n: 2 },
    });
    await userEvent.click(coordination());
    await userEvent.click(
      screen.getByRole("option", {
        name: "Independent · this policy draws its own sample",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sampling policy" }),
    );
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    const restored = axios.post.mock.calls[1][1].sampling_policy;
    expect(restored).not.toHaveProperty("coordination");
    expect(restored).not.toHaveProperty("panel");
    expect(restored).not.toHaveProperty("prn_start");
    expect(restored).toEqual(policy);
  });
});

describe("planned cell quotas in the real editor", () => {
  it("applies explicitly, invalidates the preview and clears overrides on a rule edit", async () => {
    const identity = '["default",[]]';
    axios.post.mockResolvedValue({
      data: {
        result: {
          strata: [
            {
              stratum: identity,
              eligible_units: 100,
              selected_units: 10,
              expected_units: 10,
              selected_rows: 20,
            },
          ],
          expected_evaluations: null,
        },
      },
    });
    render(<Harness policy={defaultSamplingPolicy()} />);
    const preview = () =>
      screen.getByRole("button", { name: "Preview sampling policy" });
    await userEvent.click(preview());
    await userEvent.click(
      await screen.findByText("Precision planner · advisory"),
    );
    const budget = screen.getByLabelText("Total unit budget");
    await userEvent.clear(budget);
    await userEvent.type(budget, "25");
    await userEvent.click(
      screen.getByRole("button", { name: "Suggest quotas" }),
    );
    expect(axios.post).toHaveBeenCalledTimes(1);
    await userEvent.click(
      screen.getByRole("button", { name: "Apply suggested quotas" }),
    );
    expect(
      screen.queryByText("Precision planner · advisory"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "1 explicit cell quota",
    );
    await userEvent.click(preview());
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post.mock.calls[1][1].sampling_policy.cell_quotas).toEqual({
      [identity]: 25,
    });
    const rate = screen.getByRole("spinbutton", { name: "Rate (%)" });
    await userEvent.clear(rate);
    await userEvent.type(rate, "30");
    expect(
      screen.queryByText("Precision planner · advisory"),
    ).not.toBeInTheDocument();
    await userEvent.click(preview());
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(3));
    expect(axios.post.mock.calls[2][1].sampling_policy).not.toHaveProperty(
      "cell_quotas",
    );
  });
});
