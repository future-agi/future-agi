import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const query = vi.hoisted(() => ({ data: null }));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: query.data, isLoading: false, isError: false }),
  keepPreviousData: (data) => data,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));

import TracesView from "../TracesView";

afterEach(cleanup);

describe("trace cost display", () => {
  it.each([
    [0, "$0.00"],
    [0.00003054, "$0.00003054"],
    [0.00000001, "$0.00000001"],
    [-0.00003054, "-$0.00003054"],
    [1.25, "$1.25"],
  ])(
    "uses the same money text in the summary and row for %s",
    (value, text) => {
      query.data = {
        count: 1,
        summary: { traces: 1, errors: 0, cost_usd: value },
        results: [
          {
            trace_id: "trace-one",
            status: "ok",
            cost_usd: value,
            failed_evals: [],
          },
        ],
        failed_evals: [],
      };
      render(<TracesView experimentId="experiment-one" runId="run-one" />);
      expect(screen.getByTestId("run-traces-summary")).toHaveTextContent(
        `1 trace · 0 errors · ${text}`,
      );
      expect(within(screen.getByRole("table")).getByText(text)).toBeVisible();
    },
  );
});
