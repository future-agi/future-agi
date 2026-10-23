import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import axios from "src/utils/axios";
import SamplingAuditPanel from "./SamplingAuditPanel";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    project: { samplingAudit: () => "/tracer/eval-task/sampling_audit/" },
  },
}));

const renderPanel = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SamplingAuditPanel taskId="task-1" />
    </QueryClientProvider>,
  );

const stratum = (name, locale) =>
  JSON.stringify([name, locale ? [JSON.stringify([true, locale])] : []]);

const row = (overrides) => ({
  target: null,
  expected_size: null,
  shortage: null,
  exact: null,
  srm_z: null,
  srm_p_value: null,
  srm_flagged: null,
  population_check: "match",
  ...overrides,
});

const CLOSED = {
  cohort_id: "c1",
  policy_version: "v",
  mode: "closed",
  sampling_unit: "session",
  coordination: "project",
  frozen_at: "2026-10-22T10:00:00Z",
  complete: true,
  population_source: "frozen_snapshot",
  strata: [
    row({
      stratum: stratum("rare", "en"),
      rule: "rare",
      population_size: 3,
      target: 5,
      expected_size: 3,
      achieved: 3,
      shortage: 2,
      exact: true,
      inclusion_probability: 1,
      design_weight: 1,
    }),
    row({
      stratum: stratum("default", "fr"),
      rule: "default",
      population_size: 20,
      target: 3,
      expected_size: 3,
      achieved: 3,
      shortage: 0,
      exact: true,
      inclusion_probability: 0.15,
      design_weight: 6.666666666666667,
    }),
  ],
  summary: {
    strata: 2,
    population_size: 23,
    achieved: 6,
    kish_effective_n: 3.9384615384615387,
    design_effect: 1.5234375,
    weight_cv: 0.7234,
    weighted_population: 23,
    represented_population: 23,
    unrepresented_strata: 0,
    unrepresented_population: 0,
    flagged_strata: 0,
    integrity: "ok",
    srm_alpha: 0.0005,
  },
  balance: [
    {
      attribute: "slice",
      source: null,
      categories: [
        {
          value: "default",
          present: true,
          population_size: 20,
          achieved: 3,
          population_share: 20 / 23,
          sample_share: 0.5,
          weighted_share: 20 / 23,
          standardized_difference: -0.84,
          weighted_standardized_difference: 0,
        },
      ],
      chi_square: 4.5,
      degrees_of_freedom: 1,
      p_value: 0.0339,
      max_abs_standardized_difference: 0.84,
      max_abs_weighted_standardized_difference: 0,
      weighted_imbalance: false,
    },
  ],
  notes: ["Population per stratum is re-read at the cohort's freeze time."],
};

beforeEach(() => vi.clearAllMocks());

describe("Sampling audit panel", () => {
  it("shows the frozen design, weights, shortage and balance read-only", async () => {
    axios.get.mockResolvedValue({ data: { result: CLOSED } });
    renderPanel();
    expect(
      await screen.findByText(
        "Every stratum holds exactly its quota and the frozen population still reproduces.",
      ),
    ).toBeVisible();
    expect(axios.get).toHaveBeenCalledWith(
      "/tracer/eval-task/sampling_audit/",
      { params: { eval_task_id: "task-1" } },
    );
    expect(screen.getByText("3.9")).toBeVisible(); // Kish effective n
    expect(screen.getByText("1.523")).toBeVisible(); // design effect
    const strata = screen.getByRole("table", {
      name: "Sampling audit by stratum",
    });
    const rare = within(strata).getByText("rare · en").closest("tr");
    expect(
      within(rare)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["rare · en", "3", "5", "3", "2", "1", "1", "Exact"]);
    expect(
      within(strata).getByText("All remaining contacts · fr"),
    ).toBeVisible();
    const balance = screen.getByRole("table", { name: "Balance on slice" });
    expect(within(balance).getByText("All remaining contacts")).toBeVisible();
    // Population share and weighted share are equal: weights restore it.
    expect(within(balance).getAllByText("87%")).toHaveLength(2);
    expect(within(balance).getByText("50%")).toBeVisible();
    expect(
      screen.getByText(/Weighted shares restore the population mix/),
    ).toBeVisible();
    // Advisory only: no control in the panel edits the policy or cohort.
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    // Never collides with the cohort-sampling flow's preview table locator.
    expect(
      screen.queryByRole("columnheader", { name: /eligible units/i }),
    ).not.toBeInTheDocument();
  });

  it("flags a live stratum whose selections drift from its rate", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          ...CLOSED,
          mode: "live",
          frozen_at: null,
          population_source: "persisted_decisions",
          strata: [
            row({
              stratum: stratum("default"),
              rule: "default",
              population_size: 2000,
              expected_size: 200,
              achieved: 140,
              inclusion_probability: 0.1,
              design_weight: 10,
              population_check: "persisted",
              srm_z: -4.47,
              srm_p_value: 3.07e-6,
              srm_flagged: true,
            }),
          ],
          summary: {
            ...CLOSED.summary,
            flagged_strata: 1,
            integrity: "attention",
          },
        },
      },
    });
    renderPanel();
    expect(
      await screen.findByText("1 stratum(s) need attention (see Check)."),
    ).toBeVisible();
    expect(screen.getByText("Mismatch p=3.1e-6")).toBeVisible();
  });

  it("explains a missing cohort instead of failing", async () => {
    axios.get.mockRejectedValue({
      response: {
        status: 404,
        data: {
          message:
            "No sampling cohort exists for this task's current policy yet. It is created when the task first runs.",
        },
      },
    });
    renderPanel();
    expect(await screen.findByText(/No sampling cohort exists/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });
});
