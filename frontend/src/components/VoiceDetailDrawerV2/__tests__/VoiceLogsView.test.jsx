import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import VoiceLogsView from "../VoiceLogsView";
import axios from "src/utils/axios";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    testExecutions: {
      getDetailLogs: (id) => `/simulate/call-executions/${id}/logs/`,
    },
  },
}));

const renderWithQueryClient = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

describe("VoiceLogsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches simulation call logs from the call-execution logs endpoint", async () => {
    axios.get.mockResolvedValueOnce({
      data: {
        count: 1,
        results: {
          source: "customer",
          ingestion_pending: false,
          results: [
            {
              id: "log-1",
              logged_at: "2026-05-10T12:00:00.000Z",
              severity_text: "INFO",
              category: "model",
              body: "LLM responded",
              attributes: { category: "model" },
              payload: { body: "LLM responded" },
            },
          ],
        },
      },
    });

    renderWithQueryClient(
      <VoiceLogsView module="simulate" callLogId="call-1" />,
    );

    expect(await screen.findByText("LLM responded")).toBeInTheDocument();
    expect(screen.getByText("1 logs")).toBeInTheDocument();
    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledWith(
        "/simulate/call-executions/call-1/logs/",
        {
          params: {
            page: 1,
            search: "",
          },
        },
      );
    });
  });

  it("shows a pending state while backend log ingestion is running", async () => {
    axios.get.mockResolvedValue({
      data: {
        count: 0,
        results: {
          source: "customer",
          ingestion_pending: true,
          results: [],
        },
      },
    });

    renderWithQueryClient(
      <VoiceLogsView module="simulate" callLogId="call-1" />,
    );

    expect(
      await screen.findByText("Collecting logs from provider"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("This usually takes a few seconds"),
    ).toBeInTheDocument();
  });

  it("shows returned rows even if the backend still reports ingestion pending", async () => {
    axios.get.mockResolvedValue({
      data: {
        count: 1,
        results: {
          source: "customer",
          ingestion_pending: true,
          results: [
            {
              id: "log-1",
              logged_at: "2026-05-10T12:00:00.000Z",
              severity_text: "INFO",
              category: "model",
              body: "Webhook delivered",
              attributes: { category: "model" },
              payload: { body: "Webhook delivered" },
            },
          ],
        },
      },
    });

    renderWithQueryClient(
      <VoiceLogsView module="simulate" callLogId="call-1" />,
    );

    expect(await screen.findByText("Webhook delivered")).toBeInTheDocument();
    expect(
      screen.queryByText("Collecting logs from provider"),
    ).not.toBeInTheDocument();
  });
});
