import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import axios from "src/utils/axios";
import SamplingPolicyEditor from "./SamplingPolicyEditor";
import { defaultSamplingPolicy } from "./samplingPolicyForm";
import { NewTaskValidationSchema } from "../schema";

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
  });
});
