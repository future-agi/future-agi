import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { TracerDerivedMetricRunsReadResponse } from "src/generated/api-contracts/api.zod";
import SelectedRunSummary from "../SelectedRunSummary";
import { formatMetricTime, localTime } from "../metricTime";
import { isPinnedWindowExcluded } from "../../dashboards/dashboardDateRange";

const definition = {
  version: 1,
  formula: "ratio(cost, total_tokens)",
  inputs: [],
  selection_unit: "session",
  weighting: "inverse_probability",
  definition_hash: "a".repeat(64),
  filters: [],
};
const run = {
  metric_id: "44444444-4444-4444-8444-444444444444",
  metric_version: 1,
  definition,
  window_start: "2026-10-25T23:00:00Z",
  window_end: "2026-10-26T00:00:00Z",
  as_of: "2026-10-26T00:01:00Z",
  filters: [],
  result_id: "55555555-5555-4555-8555-555555555555",
};
const result = {
  value: 2.5,
  numerator: 5,
  denominator: 2,
  status: "ok",
  unit_count: 2,
  valid_count: 1,
  missing_count: 1,
  invalid_probability_count: 0,
};

describe("immutable selected run meaning", () => {
  it.each(["none", "inverse_probability"])(
    "does not turn a %s nonboolean ratio into people counts or percent",
    (weighting) => {
      render(
        <SelectedRunSummary
          run={{ ...run, definition: { ...definition, weighting } }}
          result={result}
        />,
      );
      const summary = screen.getByRole("region", {
        name: "Selected saved run",
      });
      expect(summary).toHaveTextContent("2.5");
      expect(summary).toHaveTextContent(/numerator total/i);
      expect(summary).toHaveTextContent("5");
      expect(summary).not.toHaveTextContent(
        /5 of 2|250%|satisfies the numerator/,
      );
      expect(summary).toHaveTextContent(/1 missing inputs/);
      expect(summary).toHaveTextContent(/Point estimate only/);
      expect(summary).not.toHaveTextContent(/95% interval 0/);
    },
  );
  it("keeps zero as a value and NULL as unavailable", () => {
    const view = render(
      <SelectedRunSummary
        run={run}
        result={{ ...result, value: 0, numerator: 0 }}
      />,
    );
    expect(screen.getByRole("heading", { name: "0.00" })).toBeInTheDocument();
    view.rerender(
      <SelectedRunSummary
        run={run}
        result={{
          ...result,
          value: null,
          status: "zero_denominator",
          denominator: 0,
        }}
      />,
    );
    expect(screen.getByText(/No value — zero denominator/)).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "0.00" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Point estimate only/)).not.toBeInTheDocument();
  });
  it("summarizes active frozen task, filter and cohort settings without opening the recipe", () => {
    render(
      <SelectedRunSummary
        run={{
          ...run,
          source_run_id: "77777777-7777-4777-8777-777777777777",
          filters: [{ field: "status", value: "ok" }],
          definition: {
            ...definition,
            cohort_key: "team",
            filters: [{ field: "model", value: "saved-model" }],
          },
        }}
        result={result}
      />,
    );
    expect(
      screen.getByText("Evaluation task: 77777777-7777-4777-8777-777777777777"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/1 definition filters · 1 run filters · Cohort: team/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Saved definition · version/).parentElement,
    ).not.toHaveAttribute("open");
  });
  it("does not invent provenance on an older backend", () => {
    render(
      <SelectedRunSummary
        run={{ ...run, definition: undefined }}
        result={result}
      />,
    );
    expect(
      screen.getByText(/Saved definition unavailable/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Unweighted/)).not.toBeInTheDocument();
  });
  it("accepts the source-generated historical definition contract and rejects malformed provenance", () => {
    expect(TracerDerivedMetricRunsReadResponse.parse(run).definition).toEqual(
      definition,
    );
    expect(() =>
      TracerDerivedMetricRunsReadResponse.parse({
        ...run,
        definition: { ...definition, version: "not a version" },
      }),
    ).toThrow();
    expect(
      TracerDerivedMetricRunsReadResponse.parse({
        ...run,
        definition: undefined,
      }).definition,
    ).toBeUndefined();
  });
});

describe("explicit time boundaries", () => {
  it("normalizes offsets across midnight and DST changes to explicitly labeled UTC", () => {
    expect(formatMetricTime("2026-10-25T20:30:00-07:00")).toBe(
      "26 Oct 2026, 03:30:00 UTC",
    );
    expect(formatMetricTime("2026-11-01T01:30:00-07:00")).toBe(
      "01 Nov 2026, 08:30:00 UTC",
    );
    expect(formatMetricTime("2026-11-01T01:30:00-08:00")).toBe(
      "01 Nov 2026, 09:30:00 UTC",
    );
    expect(formatMetricTime("2026-03-08T03:30:00-07:00")).toBe(
      "08 Mar 2026, 10:30:00 UTC",
    );
    expect(formatMetricTime("")).toBe("Unavailable");
    expect(formatMetricTime("invalid")).toBe("Unavailable");
  });
  it("native datetime values preserve local clock components rather than being labeled UTC", () => {
    const date = new Date(2026, 9, 25, 20, 30);
    expect(localTime(date)).toBe("2026-10-25T20:30");
    expect(new Date(localTime(date)).getTime()).toBe(date.getTime());
  });
  it("proves pinned exclusion only for finite disjoint ranges, never overlap or unknown data", () => {
    const config = {
      metrics: [{ type: "derived_metric", derived_run_id: "run" }],
      time_range: {
        custom_start: run.window_start,
        custom_end: run.window_end,
      },
    };
    expect(
      isPinnedWindowExcluded(config, {
        start: "2026-10-23",
        end: "2026-10-24",
      }),
    ).toBe(true);
    expect(
      isPinnedWindowExcluded(config, {
        start: run.window_end,
        end: "2026-10-27",
      }),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(config, {
        start: run.window_start,
        end: run.window_end,
      }),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(config, { start: "invalid", end: "2026-10-27" }),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(
        { ...config, time_range: { preset: "7D" } },
        { start: "2026-10-23", end: "2026-10-24" },
      ),
    ).toBe(false);
    expect(isPinnedWindowExcluded(config, null)).toBe(false);
  });
});
