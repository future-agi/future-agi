import React from "react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import MetricContributors from "../MetricContributors";
import { contributorLink } from "../contributorLinks";

const get = vi.hoisted(() => vi.fn());
vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    derivedMetrics: { contributors: (id) => `/runs/${id}/contributors/` },
  },
}));

it("keeps drill-down optional, bounded, and scoped in its query", async () => {
  get.mockResolvedValue({
    data: {
      result_id: "00000000-0000-0000-0000-000000000004",
      rollup: "overall",
      group_key: "",
      diagnostics: {
        valid_count: 1,
        missing_count: 2,
        invalid_probability_count: 3,
        undefined_arithmetic_count: 0,
        missing_by_input: { score: 2 },
      },
      results: [
        {
          unit_id: "session-1",
          session_id: "session-1",
          trace_id: null,
          weight: 2,
          numerator: 0,
          denominator: 1,
          influence: -0.25,
        },
      ],
      truncated: true,
    },
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MetricContributors
        scope="org:workspace"
        runId="run"
        resultId="00000000-0000-0000-0000-000000000004"
        projectId="project"
        rollup="overall"
        groups={[{ group_key: "" }]}
      />
    </QueryClientProvider>,
  );
  expect(get).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Inspect contributors" }));
  await waitFor(() =>
    expect(
      screen.getByRole("table", { name: "Metric contributors" }),
    ).toBeInTheDocument(),
  );
  expect(get).toHaveBeenCalledWith(
    "/runs/run/contributors/",
    expect.objectContaining({
      params: { rollup: "overall", group_key: "", limit: 20 },
    }),
  );
  expect(
    screen.getByText(/1 valid · 2 missing · 3 invalid probability/),
  ).toBeInTheDocument();
  expect(screen.getByText(/Showing the 20 largest/)).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "session-1" }).getAttribute("href"),
  ).toContain("/dashboard/observe/project/sessions?");
});

describe("contributor source links", () => {
  it("encodes identities as scoped filters, never executable links", () => {
    const link = contributorLink("project", { trace_id: "id&x=<script>" });
    const url = new URL(link, "https://example.test");
    expect(url.pathname).toBe("/dashboard/observe/project/llm-tracing");
    expect(
      JSON.parse(url.searchParams.get("primaryTraceFilter"))[0].filter_config
        .filter_value,
    ).toBe("id&x=<script>");
  });
  it("has no invented link when identities are absent", () => {
    expect(contributorLink("project", {})).toBeNull();
  });
});
