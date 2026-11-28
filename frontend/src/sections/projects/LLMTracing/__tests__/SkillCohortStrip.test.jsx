import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import { forbiddenRuntimeWords } from "src/sections/skills/runtime/runtimeCopy";
import SkillCohortStrip, { cohortMetrics, mapCohortSummary } from "../SkillCohortStrip";

const http = vi.hoisted(() => ({ post: vi.fn(), endpoint: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { post: http.post, get: vi.fn() },
  traceSkillsEndpoint: http.endpoint,
}));

const V3 = "33333333-3333-4333-8333-333333333333";
const FILTERS = [
  { column_id: "skill.version_id", filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: V3 } },
  { column_id: "created_at", filter_config: { filter_type: "datetime", filter_op: "between", filter_value: ["2026-11-10T00:00:00Z", "2026-11-17T00:00:00Z"] } },
];
const SUMMARY = { traces: 1204, failed_requests: { count: 31, rate: 0.0257 }, latency_ms: { p95: 2400 }, cost: { per_trace_mean: 0.0123 }, incomplete: 12 };

const strip = (props = {}) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SkillCohortStrip projectId="p1" filters={FILTERS} from="2026-11-10T00:00:00Z" to="2026-11-17T00:00:00Z" {...props} />
    </QueryClientProvider>,
  );

beforeEach(() => {
  http.post.mockReset();
  http.endpoint.mockReset();
  http.endpoint.mockReturnValue("/tracer/trace/skill-projection/");
});

describe("mapCohortSummary", () => {
  it("reads the four numbers and the low-sample rule", () => {
    expect(mapCohortSummary(SUMMARY)).toEqual({ traces: 1204, failedCount: 31, failedRate: 0.0257, p95: 2400, costPerTrace: 0.0123, incomplete: 12, lowSample: false });
    expect(mapCohortSummary({ traces: 12 }).lowSample).toBe(true);
    expect(mapCohortSummary({ traces: "lots" })).toBeNull();
  });

  it("formats them with the runtime copy", () => {
    expect(cohortMetrics(mapCohortSummary(SUMMARY)).map((metric) => [metric.label, metric.value])).toEqual([
      ["Traces", "1,204"],
      ["Failed requests", "31 · 2.6%"],
      ["p95", "2.4 s"],
      ["Cost per trace", "$0.012"],
    ]);
  });
});

describe("SkillCohortStrip", () => {
  it("sends the whole filter request and shows the cohort's numbers", async () => {
    http.post.mockResolvedValue({ data: { result: { summary: SUMMARY } } });
    const { container } = strip();
    expect(await screen.findByRole("region", { name: "Traces matching these filters" })).toHaveTextContent("1,204");
    expect(http.post).toHaveBeenCalledWith(
      "/tracer/trace/skill-projection/",
      { projection: "cohort", project_id: "p1", filters: FILTERS, from: "2026-11-10T00:00:00Z", to: "2026-11-17T00:00:00Z" },
      expect.objectContaining({ signal: expect.anything() }),
    );
    expect(screen.getByText("12 traces have no root span yet and aren't counted.")).toBeInTheDocument();
    expect(forbiddenRuntimeWords(container.textContent)).toEqual([]);
  });

  it("is hidden without a SKILL item", () => {
    const { container } = strip({ filters: [FILTERS[1]] });
    expect(container.textContent).toBe("");
    expect(http.post).not.toHaveBeenCalled();
  });

  it("replaces the strip with one line when the read fails", async () => {
    http.endpoint.mockImplementation(() => {
      const error = new Error("pending");
      error.code = "runtime_route_pending";
      throw error;
    });
    strip();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Couldn't load the numbers for these traces."));
  });

  it("says a slow read took too long", async () => {
    http.post.mockRejectedValue({ response: { status: 503, data: { code: "runtime_read_budget_exceeded" } } });
    strip();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("This took too long. Try a shorter window."));
  });
});
