import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestTable from "./RequestTable";

vi.mock("./hooks/useRequestLogs", () => ({
  default: () => ({
    data: {
      results: [
        {
          id: "log-row-id",
          request_id: "request-1",
          model: "gpt-4o-mini",
          provider: "openai",
          status_code: 200,
          latency_ms: 456,
          cost: "0.001000",
          input_tokens: 10,
          output_tokens: 12,
          total_tokens: 22,
          session_id: "session-1",
          started_at: "2026-05-21T10:01:00Z",
          cache_hit: true,
          guardrail_triggered: true,
          fallback_used: true,
        },
      ],
      count: 1,
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

describe("RequestTable", () => {
  it("renders request-log rows using canonical snake_case API fields", () => {
    const onSelectLog = vi.fn();

    render(
      <RequestTable
        filters={{}}
        setFilter={vi.fn()}
        setFilters={vi.fn()}
        onSelectLog={onSelectLog}
      />,
    );

    const table = within(screen.getByRole("table"));
    expect(table.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(table.getByText("openai")).toBeInTheDocument();
    expect(table.getByText("456ms")).toBeInTheDocument();
    expect(table.getByText("10 / 12")).toBeInTheDocument();
    expect(table.getByText("session-1")).toBeInTheDocument();
    expect(table.queryByText("N/A")).not.toBeInTheDocument();

    table.getByText("gpt-4o-mini").closest("tr").click();
    expect(onSelectLog).toHaveBeenCalledWith("log-row-id");
  });
});
