import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import SkillVersionGraph, { skillVersionSeries } from "../SkillVersionGraph";
import { parseTraceGraphResponse } from "src/api/project/observe-contracts";

vi.mock("react-apexcharts", () => ({ default: ({ series }) => <div aria-label={series[0].name}>{JSON.stringify(series[0].data)}</div> }));

const result = {
  metric_name: "Cost per trace", metric_statistic: "mean", split_by: "skill_version",
  query_complete: true, query_exact: true, query_sampled: false, query_status: "complete",
  data: [], totals_note: "cohorts overlap; not additive",
  series: [1, 2].map((version) => ({
    version_id: `00000000-0000-4000-8000-00000000000${version}`, label: `Help v${version}`,
    data: [{ timestamp: "2026-11-01T00:00:00+00:00", value: version === 1 ? 1.25 : null, primary_traffic: 12, traces: 12, incomplete: 0 }],
  })),
};

describe("skill version graph response and rendering", () => {
  it("retains typed nullable series through the generated envelope", () => {
    expect(parseTraceGraphResponse({ status: true, result })).toEqual(result);
    expect(() => parseTraceGraphResponse({ status: true, result: { ...result, series: [{ ...result.series[0], version_id: "not-an-id" }] } })).toThrow();
  });

  it("renders separate named panels, low samples and inspectable values without adding cohorts", () => {
    render(<SkillVersionGraph result={result} />);
    expect(screen.getByLabelText("Help v1")).toHaveTextContent("1.25");
    expect(screen.getByLabelText("Help v2")).toHaveTextContent("null");
    expect(screen.getByText(/Cohorts overlap; not additive/)).toBeInTheDocument();
    expect(screen.getAllByText("12 · low sample")).toHaveLength(2);
    expect(screen.getByRole("table", { hidden: true })).toHaveTextContent("Missing roots");
  });

  it("never renders partial or unsplit data as exact cohorts", () => {
    expect(skillVersionSeries({ ...result, query_complete: false, query_status: "degraded" })).toEqual([]);
    expect(skillVersionSeries({ ...result, split_by: undefined })).toEqual([]);
    expect(skillVersionSeries({ ...result, series: [] })).toEqual([]);
  });
});
