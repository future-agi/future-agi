import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import SessionEvalsList from "../SessionEvalsList";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    project: {
      getSessionEvalLogs: (id) => `/sessions/${id}/eval-logs/`,
    },
  },
}));
vi.mock("src/components/error-state", () => ({
  default: ({ title, detail }) =>
    React.createElement("div", { role: "alert" }, title, " ", detail),
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

const endpoint = (id) => `/sessions/${id}/eval-logs/`;
const makeLogs = (total, sessionId = "session-a") =>
  Array.from({ length: total }, (_, index) => ({
    id: `${sessionId}-log-${index}`,
    eval_name: `${sessionId} evaluation ${total - index}`,
    score: null,
    status: "success",
    result: "Passed",
  }));
const response = (logs, page) => ({
  data: {
    result: {
      total: logs.length,
      page,
      page_size: 100,
      items: logs.slice(page * 100, (page + 1) * 100),
    },
  },
});
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

let client;
const sessionUI = (sessionId) =>
  React.createElement(
    QueryClientProvider,
    { client },
    React.createElement(SessionEvalsList, { sessionId }),
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

afterEach(() => {
  cleanup();
  client.clear();
});

describe("SessionEvalsList pagination", () => {
  it.each([101, 200, 201])(
    "loads all %i results in order and stops at total",
    async (total) => {
      const logs = makeLogs(total);
      axios.get.mockImplementation((_url, { params }) =>
        Promise.resolve(response(logs, params.page)),
      );
      render(sessionUI("session-a"));

      await screen.findByText(logs[0].eval_name);
      expect(axios.get).toHaveBeenCalledTimes(1);
      expect(axios.get).toHaveBeenLastCalledWith(endpoint("session-a"), {
        params: { page: 0, page_size: 100 },
      });
      expect(screen.queryByText(logs[100].eval_name)).not.toBeInTheDocument();

      for (let page = 1; page < Math.ceil(total / 100); page += 1) {
        fireEvent.click(screen.getByRole("button", { name: "Load more" }));
        const lastIndex = Math.min(total, (page + 1) * 100) - 1;
        await screen.findByText(logs[lastIndex].eval_name);
        expect(axios.get).toHaveBeenLastCalledWith(endpoint("session-a"), {
          params: { page, page_size: 100 },
        });
        expect(screen.getAllByText(/^session-a evaluation /)).toHaveLength(
          lastIndex + 1,
        );
      }

      expect(
        screen
          .getAllByText(/^session-a evaluation /)
          .map((row) => row.textContent),
      ).toEqual(logs.map((log) => log.eval_name));
      expect(axios.get).toHaveBeenCalledTimes(Math.ceil(total / 100));
      expect(
        screen.queryByRole("button", { name: "Load more" }),
      ).not.toBeInTheDocument();
    },
  );

  it("keeps rows visible and disables load more while the next page is pending", async () => {
    const logs = makeLogs(101);
    const next = deferred();
    axios.get
      .mockResolvedValueOnce(response(logs, 0))
      .mockReturnValueOnce(next.promise);
    render(sessionUI("session-a"));
    await screen.findByText(logs[0].eval_name);

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    const loading = await screen.findByRole("button", {
      name: "Loading more...",
    });
    expect(loading).toBeDisabled();
    expect(screen.getAllByText(/^session-a evaluation /)).toHaveLength(100);
    fireEvent.click(loading);
    expect(axios.get).toHaveBeenCalledTimes(2);

    await act(async () => next.resolve(response(logs, 1)));
    await screen.findByText(logs[100].eval_name);
    expect(screen.getAllByText(/^session-a evaluation /)).toHaveLength(101);
    expect(
      screen.queryByRole("button", { name: "Loading more..." }),
    ).not.toBeInTheDocument();
  });

  it("preserves loaded rows and retries the failed next page", async () => {
    const logs = makeLogs(101);
    axios.get
      .mockResolvedValueOnce(response(logs, 0))
      .mockRejectedValueOnce(new Error("Older evaluations unavailable"))
      .mockResolvedValueOnce(response(logs, 1));
    render(sessionUI("session-a"));
    await screen.findByText(logs[0].eval_name);

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("Failed to load more session evals");
    expect(error).toHaveTextContent("Older evaluations unavailable");
    expect(screen.getAllByText(/^session-a evaluation /)).toHaveLength(100);
    expect(screen.queryByText(logs[100].eval_name)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry loading more" }));
    await screen.findByText(logs[100].eval_name);
    expect(
      axios.get.mock.calls.map(([, config]) => config.params.page),
    ).toEqual([0, 1, 1]);
    expect(screen.getAllByText(/^session-a evaluation /)).toHaveLength(101);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
  });

  it.each(["loaded", "pending"])(
    "starts a new session at page zero with the old next page %s",
    async (state) => {
      const oldLogs = makeLogs(201);
      const newLogs = makeLogs(101, "session-b");
      const oldNext = deferred();
      axios.get.mockImplementation((url, { params }) => {
        if (url === endpoint("session-a") && params.page === 1) {
          return oldNext.promise;
        }
        return Promise.resolve(
          response(
            url === endpoint("session-a") ? oldLogs : newLogs,
            params.page,
          ),
        );
      });
      const view = render(sessionUI("session-a"));
      await screen.findByText(oldLogs[0].eval_name);
      fireEvent.click(screen.getByRole("button", { name: "Load more" }));
      await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
      if (state === "loaded") {
        await act(async () => oldNext.resolve(response(oldLogs, 1)));
        await screen.findByText(oldLogs[199].eval_name);
      }

      view.rerender(sessionUI("session-b"));
      await screen.findByText(newLogs[0].eval_name);
      expect(axios.get).toHaveBeenLastCalledWith(endpoint("session-b"), {
        params: { page: 0, page_size: 100 },
      });
      if (state === "pending") {
        await act(async () => oldNext.resolve(response(oldLogs, 1)));
      }
      expect(
        screen.queryByText(/^session-a evaluation /),
      ).not.toBeInTheDocument();
      expect(screen.getAllByText(/^session-b evaluation /)).toHaveLength(100);

      fireEvent.click(screen.getByRole("button", { name: "Load more" }));
      await screen.findByText(newLogs[100].eval_name);
      expect(axios.get).toHaveBeenLastCalledWith(endpoint("session-b"), {
        params: { page: 1, page_size: 100 },
      });
      expect(
        screen.queryByText(/^session-a evaluation /),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Load more" }),
      ).not.toBeInTheDocument();
    },
  );

  it("stops requesting pages if a later page is empty", async () => {
    const logs = makeLogs(101);
    const emptyPage = response(logs, 1);
    emptyPage.data.result.items = [];
    axios.get
      .mockResolvedValueOnce(response(logs, 0))
      .mockResolvedValueOnce(emptyPage);
    render(sessionUI("session-a"));
    await screen.findByText(logs[0].eval_name);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Load more" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Loading more..." }),
      ).not.toBeInTheDocument();
    });
    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText(/^session-a evaluation /)).toHaveLength(100);
  });
});

describe("SessionEvalsList existing states", () => {
  it.each([1, 100])("does not offer more for %i results", async (total) => {
    const logs = makeLogs(total);
    axios.get.mockResolvedValueOnce(response(logs, 0));
    render(sessionUI("session-a"));
    await screen.findByText(logs[0].eval_name);
    expect(screen.getAllByText(/^session-a evaluation /)).toHaveLength(total);
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(1);
  });

  it("keeps the initial loading and empty states", async () => {
    const initial = deferred();
    axios.get.mockReturnValueOnce(initial.promise);
    render(sessionUI("session-a"));
    expect(screen.getByRole("status")).toHaveTextContent("Loading evaluations");
    await act(async () => initial.resolve(response([], 0)));
    await screen.findByText("No session-level evaluations yet.");
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the initial request error distinct from an empty result", async () => {
    axios.get.mockRejectedValueOnce(new Error("Initial request failed"));
    render(sessionUI("session-a"));
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("Failed to load session evals");
    expect(error).toHaveTextContent(
      "Couldn't do that right now. Try again in a moment.",
    );
    expect(
      screen.queryByText("No session-level evaluations yet."),
    ).not.toBeInTheDocument();
  });

  it("does not request evaluation logs without a session id", () => {
    render(sessionUI(undefined));
    expect(axios.get).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
  });
});
