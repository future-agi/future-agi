import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "src/utils/test-utils";
import axios from "src/utils/axios";
import SessionEvalsList from "../SessionEvalsList";

// The real ErrorState renders here, so the test sees the Retry button a
// person would see.
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    project: {
      getSessionEvalLogs: (id) => `/sessions/${id}/eval-logs/`,
    },
  },
}));
vi.mock("src/components/content-skeleton", () => ({
  default: () =>
    React.createElement("div", { role: "status" }, "Loading evaluations"),
}));
vi.mock("src/components/status-badge/StatusBadge", () => ({
  default: ({ label }) => React.createElement("span", null, label),
}));
vi.mock("src/components/eval/EvalStatusIndicator", () => ({
  default: ({ status }) => React.createElement("span", null, status),
}));

const endpoint = "/sessions/session-a/eval-logs/";
const firstPage = {
  data: {
    result: {
      total: 1,
      page: 0,
      page_size: 100,
      items: [
        {
          id: "log-1",
          eval_name: "Session coherence",
          score: null,
          status: "success",
          result: "Passed",
        },
      ],
    },
  },
};
// A 5xx as the app's axios interceptor rejects it (src/utils/axios.js): the
// response body spread with statusCode. The body has no message.
const serverError = {
  status: false,
  statusCode: 503,
  transportCode: "ERR_BAD_RESPONSE",
  responseHeaders: {},
};

let client;
const sessionUI = () => (
  <QueryClientProvider client={client}>
    <SessionEvalsList sessionId="session-a" />
  </QueryClientProvider>
);

beforeEach(() => {
  axios.get.mockReset();
  client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
        staleTime: Infinity,
        refetchOnWindowFocus: false,
      },
    },
  });
});

describe("SessionEvalsList first page error", () => {
  it("says what to do next and retries the first page", async () => {
    axios.get
      .mockRejectedValueOnce(serverError)
      .mockResolvedValueOnce(firstPage);
    render(sessionUI());

    expect(
      await screen.findByText("Failed to load session evals"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Couldn't do that right now. Try again in a moment."),
    ).toBeInTheDocument();
    expect(screen.queryByText("unknown error")).not.toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toBeEnabled();

    fireEvent.click(retry);

    expect(await screen.findByText("Session coherence")).toBeInTheDocument();
    expect(
      screen.queryByText("Failed to load session evals"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry" }),
    ).not.toBeInTheDocument();
    expect(
      axios.get.mock.calls.map(([url, config]) => [url, config.params.page]),
    ).toEqual([
      [endpoint, 0],
      [endpoint, 0],
    ]);
  });
});
