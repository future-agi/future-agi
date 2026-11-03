import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import SamplingPolicyEditor from "./SamplingPolicyEditor";
import {
  defaultSamplingPolicy,
  samplingPolicyErrors,
  samplingPolicyForApi,
} from "./samplingPolicyForm";
import { NewTaskValidationSchema, getNewTaskFilters } from "../schema";
import {
  SAMPLING_PREVIEW_SCOPE,
  useSamplingPreview,
} from "./samplingPreviewSummary";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: { project: { samplingPreview: () => "/sample-preview/" } },
}));
let form;
const values = (policy) => ({
  name: "cohort",
  project: "project-id",
  samplingPolicy: policy,
  evalsDetails: [{ id: "eval" }],
  samplingRate: 50,
  spansLimit: 100,
  rowType: "spans",
  runType: policy?.mode === "live" ? "continuous" : "historical",
  datePreset: "Custom",
  filters: [],
  startDate: "2026-10-01T00:00:00.000Z",
  endDate: "2026-10-02T00:00:00.000Z",
});
function Harness({
  policy = { ...defaultSamplingPolicy(), default: { target_n: 2 } },
  onSubmit = () => {},
}) {
  form = useForm({
    defaultValues: values(policy),
    resolver: zodResolver(NewTaskValidationSchema()),
  });
  const [summary] = useSamplingPreview(form.control);
  return (
    <>
      <SamplingPolicyEditor control={form.control} getValues={form.getValues} />
      <button onClick={form.handleSubmit(onSubmit)}>Save task</button>
      <output aria-label="Shared preview">
        {summary?.text || "No counts"}
      </output>
    </>
  );
}
Harness.propTypes = { policy: PropTypes.object, onSubmit: PropTypes.func };
const response = (
  strata = [{ eligible_units: 20, selected_units: 2, selected_rows: 7 }],
) => ({
  data: {
    result: {
      strata,
      evaluator_count: 2,
      expected_evaluations: 14,
      cost_note: "Pricing unavailable",
    },
  },
});
beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue(response());
});

describe("sampling preview and validation boundaries", () => {
  it.each([
    [14, "Try again in 14 seconds."],
    [0, "Try again in 0 seconds."],
    [undefined, "Please wait before trying again."],
    ["<script>14</script>", "Please wait before trying again."],
  ])(
    "gives wait advice rather than policy edits for throttling (%s)",
    async (retryAfterSeconds, message) => {
      axios.post.mockRejectedValueOnce({
        statusCode: 429,
        retryAfterSeconds,
        detail: "Check your policy",
      });
      render(<Harness />);
      await userEvent.click(
        screen.getByRole("button", { name: "Preview sample" }),
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(screen.getByRole("alert")).not.toHaveTextContent(/check.*policy/i);
      expect(
        screen.getByRole("button", { name: "Preview sample" }),
      ).toBeEnabled();
    },
  );

  it.each([
    [
      "closed",
      "How many conversations?",
      "target_n",
      "Enter the number of target units.",
      3,
    ],
    [
      "live",
      "What share of new conversations? (%)",
      "rate",
      "Enter a rate from 0 to 100.",
      25,
    ],
  ])(
    "keeps a cleared %s amount explicit, blocks preview/save and recovers without changing modes",
    async (mode, label, key, message, amount) => {
      const policy = {
        ...defaultSamplingPolicy(),
        mode,
        default: { [key]: 2 },
      };
      if (mode === "live") policy.window.end = null;
      const onSubmit = vi.fn();
      render(<Harness policy={policy} onSubmit={onSubmit} />);
      const input = screen.getByRole("spinbutton", { name: label });
      await userEvent.clear(input);
      expect(form.getValues("samplingPolicy.default")).toEqual({ [key]: "" });
      expect(screen.getByText(message)).toBeVisible();
      expect(
        screen.getByRole("button", { name: "Preview sample" }),
      ).toBeDisabled();
      await userEvent.click(screen.getByRole("button", { name: "Save task" }));
      expect(onSubmit).not.toHaveBeenCalled();
      expect(axios.post).not.toHaveBeenCalled();
      await userEvent.type(input, String(amount));
      expect(screen.queryByText(message)).not.toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", { name: "Preview sample" }),
      );
      expect(axios.post.mock.calls[0][1].sampling_policy.default).toEqual({
        [key]: amount,
      });
      await userEvent.click(screen.getByRole("button", { name: "Save task" }));
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(onSubmit.mock.calls[0][0].samplingPolicy.default).toEqual({
        [key]: amount,
      });
    },
  );

  it.each([
    [{ rate: "" }, "default.rate"],
    [{ target_n: "" }, "default.target_n"],
    [{}, "default.rate"],
    [{ rate: Number.NaN }, "default.rate"],
    [{ target_n: 1.5 }, "default.target_n"],
    [{ rate: 10, max_n: "" }, "default.max_n"],
  ])(
    "rejects the invalid quota %j at the unchanged save boundary",
    (rule, path) => {
      const result = NewTaskValidationSchema().safeParse(
        values({ ...defaultSamplingPolicy(), default: rule }),
      );
      expect(result.success).toBe(false);
      expect(
        result.error.issues.map((issue) => issue.path.join(".")),
      ).toContain(`samplingPolicy.${path}`);
    },
  );

  it.each([
    ["0.", "5", 0.5],
    ["-", "5", -5],
    ["1e-", "2", 0.01],
  ])(
    "preserves opaque numeric draft %s and normalizes only complete wire copies",
    (prefix, suffix, expected) => {
      const policy = {
        ...defaultSamplingPolicy(),
        strata: [
          {
            name: "costly",
            rate: 50,
            filters: [
              { source: "span", key: "cost", op: "gt", values: [prefix] },
            ],
          },
        ],
      };
      const before = structuredClone(policy);
      expect(samplingPolicyErrors(policy)).toHaveProperty(
        "strata.0.filters.0.values",
      );
      expect(() => samplingPolicyForApi(policy)).toThrow();
      expect(policy).toEqual(before);
      policy.strata[0].filters[0].values = [prefix + suffix];
      const wire = samplingPolicyForApi(policy);
      expect(wire.strata[0].filters[0].values).toEqual([expected]);
      expect(policy.strata[0].filters[0].values).toEqual([prefix + suffix]);
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
    (...predicateValues) => {
      const policy = {
        ...defaultSamplingPolicy(),
        strata: [
          {
            name: "costly",
            rate: 50,
            filters: [
              {
                source: "span",
                key: "cost",
                op: "gt",
                values: predicateValues,
              },
            ],
          },
        ],
      };
      const result = NewTaskValidationSchema().safeParse(values(policy));
      expect(result.success).toBe(false);
      expect(
        result.error.issues.map((issue) => issue.path.join(".")),
      ).toContain("samplingPolicy.strata.0.filters.0.values");
    },
  );

  it("uses only the policy window for preview while preserving the legacy percentage date range", async () => {
    const policy = defaultSamplingPolicy();
    render(<Harness policy={policy} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    const sent = axios.post.mock.calls[0][1];
    expect(sent.sampling_policy).toEqual(policy);
    expect(sent.filters).not.toHaveProperty("date_range");
    expect(sent.filters).not.toHaveProperty("date_preset");
    const legacy = getNewTaskFilters({ ...values(null) }, "project-id").filters;
    expect(legacy.date_range).toEqual([
      values(null).startDate,
      values(null).endDate,
    ]);
  });

  it("distinguishes unavailable membership from a genuine zero and actual rows from expected live work", async () => {
    const policy = {
      ...defaultSamplingPolicy(),
      mode: "live",
      window: { start: "2026-10-01T00:00:00Z", end: null },
    };
    render(<Harness policy={policy} />);
    axios.post.mockResolvedValueOnce(
      response([{ eligible_units: 2, selected_rows: 7 }]),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(
      await screen.findByLabelText("Sampling preview summary"),
    ).toHaveTextContent("Unavailable");
    expect(screen.getByLabelText("Shared preview")).toHaveTextContent(
      "No counts",
    );
    axios.post.mockResolvedValueOnce(
      response([{ eligible_units: 20, selected_units: 0, selected_rows: 0 }]),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(
      await screen.findByLabelText("Sampling preview summary"),
    ).toHaveTextContent("0 conversations · 0 spans evaluated");
    await userEvent.click(screen.getByText("Evaluation work"));
    expect(screen.getByText(/Expected evaluation runs: 14/)).toBeVisible();
    expect(screen.getByLabelText("Shared preview")).toHaveTextContent(
      "0 of 20 sessions; 0 spans × 2 evaluators = 0 runs",
    );
  });

  it.each(SAMPLING_PREVIEW_SCOPE)(
    "rejects a late preview after %s changes",
    async (name) => {
      let finish;
      axios.post.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      render(<Harness />);
      await userEvent.click(
        screen.getByRole("button", { name: "Preview sample" }),
      );
      const next = {
        project: "other",
        rowType: "traces",
        filters: [
          {
            propertyId: "service_name",
            filterConfig: { filterValue: ["new"] },
          },
        ],
        runType: "continuous",
        startDate: "2026-10-01T01:00:00Z",
        endDate: "2026-10-02T01:00:00Z",
        datePreset: "30D",
        evalsDetails: [{ id: "other" }],
        samplingPolicy: {
          ...defaultSamplingPolicy(),
          default: { target_n: 50 },
        },
      }[name];
      await act(async () => form.setValue(name, next));
      await act(async () => finish(response()));
      expect(
        screen.queryByLabelText("Sampling preview summary"),
      ).not.toBeInTheDocument();
      expect(screen.getByLabelText("Shared preview")).toHaveTextContent(
        "No counts",
      );
    },
  );

  it("clears successful counts on retry failure instead of showing stale evidence", async () => {
    render(<Harness />);
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(
      await screen.findByLabelText("Sampling preview summary"),
    ).toHaveTextContent("2 conversations · 7 spans evaluated");
    axios.post.mockRejectedValueOnce(new Error("offline"));
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(
      screen.queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Shared preview")).toHaveTextContent(
      "No counts",
    );
  });
});
