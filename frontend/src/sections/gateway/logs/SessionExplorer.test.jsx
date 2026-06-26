import React from "react";
import { useQuery } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import SessionExplorer from "./SessionExplorer";

const mockUseSessions = vi.hoisted(() => vi.fn());

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(),
}));

vi.mock("./hooks/useSessions", () => ({
  default: mockUseSessions,
}));

describe("SessionExplorer", () => {
  it("renders session aggregate and detail rows using canonical snake_case fields", async () => {
    const onRequestClick = vi.fn();
    mockUseSessions.mockReturnValue({
      data: {
        results: [
          {
            session_id: "session-aggregate-1",
            request_count: 2,
            total_cost: "0.010000",
            total_tokens: 120,
            avg_latency: 321,
            first_request_at: "2026-05-21T10:00:00Z",
            last_request_at: "2026-05-21T10:05:00Z",
            error_count: 1,
            models: ["gpt-4o-mini"],
            providers: ["openai"],
          },
        ],
        count: 1,
      },
      isLoading: false,
    });
    vi.mocked(useQuery).mockReturnValue({
      data: {
        results: [
          {
            id: "log-row-id",
            request_id: "request-detail-1",
            model: "gpt-4o-mini",
            status_code: 500,
            latency_ms: 654,
            cost: "0.003000",
            started_at: "2026-05-21T10:04:00Z",
          },
        ],
      },
      isLoading: false,
    });

    render(<SessionExplorer filters={{}} onRequestClick={onRequestClick} />);
    expect(mockUseSessions).toHaveBeenLastCalledWith({
      filters: { ordering: "-last_request_at" },
      page: 1,
      pageSize: 25,
    });

    const sessionSummary = screen.getByRole("button", {
      name: /session-aggregate-1/,
    });
    expect(sessionSummary).toBeInTheDocument();
    expect(screen.getByText("2 requests")).toBeInTheDocument();
    expect(screen.getByText("1 errors")).toBeInTheDocument();

    await userEvent.click(sessionSummary);

    const detailTable = within(screen.getByRole("table"));
    expect(detailTable.getByText("request-deta...")).toBeInTheDocument();
    expect(detailTable.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(detailTable.getByText("654ms")).toBeInTheDocument();

    detailTable.getByText("request-deta...").closest("tr").click();
    expect(onRequestClick).toHaveBeenCalledWith("log-row-id");

    await userEvent.click(
      screen.getByRole("button", { name: "Most Requests" }),
    );
    expect(mockUseSessions).toHaveBeenLastCalledWith({
      filters: { ordering: "-request_count" },
      page: 1,
      pageSize: 25,
    });
  });
});
