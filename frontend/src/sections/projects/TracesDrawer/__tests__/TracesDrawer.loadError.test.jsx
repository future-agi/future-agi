import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import TracesDrawer from "../TracesDrawer";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  header: null,
  history: null,
  scroll: null,
}));

vi.mock("src/utils/axios", () => ({
  readQuery: (...args) => state.get(...args),
  default: { get: (...args) => state.get(...args) },
  endpoints: {
    project: { traceSessionQuery: (id) => `/sessions/${id}/query/` },
  },
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/hooks/use-scroll-end", () => ({
  useScrollEnd: (callback) => {
    state.scroll = callback;
    return { current: null };
  },
}));
vi.mock("../Header", () => ({
  default: (props) => {
    state.header = props;
    return null;
  },
}));
vi.mock("../SessionHistory", () => ({
  default: (props) => {
    state.history = props;
    // eslint-disable-next-line react/prop-types
    const ids = props.traceDetail.map((row) => row.id).join(",");
    return <div data-testid="session-history">{ids}</div>;
  },
}));
vi.mock("../SessionEvalsList", () => ({ default: () => null }));
vi.mock("../Filters", () => ({ default: () => null }));
vi.mock("src/components/InlineAnnotator", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../useTraceDrawerStore", () => ({
  useTraceDrawerStore: () => ({ viewType: "list", setViewType: vi.fn() }),
}));
vi.mock("src/utils/utils", () => ({ formatMs: (value) => value }));
vi.mock("src/utils/logger", () => ({ default: { debug: vi.fn() } }));

// The Sessions grid always opens the drawer with its originating list context.
const context = {
  workspace_id: "workspace-1",
  project_id: "project-1",
  filters: [],
  sort_params: [],
  cursor_mode: false,
};

// Rejections use the shape src/utils/axios rejects with: the response body
// spread onto the error, plus statusCode (no response body offline).
const TIMEOUT = {
  status: false,
  result:
    "Session detail unavailable: query exceeded time budget. Retry shortly.",
  statusCode: 504,
};

const page = (ids, metadata = {}, next = false) => ({
  data: {
    result: {
      response: ids.map((id) => ({ id })),
      session_metadata: { session_id: "session-1", ...metadata },
      next,
    },
  },
});

let client;
const renderDrawer = () =>
  render(
    <QueryClientProvider client={client}>
      <TracesDrawer
        open
        onClose={vi.fn()}
        rowData={{ session_id: "session-1" }}
        navigationContext={context}
      />
    </QueryClientProvider>,
  );

describe("Session drawer when the session read fails", () => {
  beforeEach(() => {
    state.get.mockReset();
    state.header = null;
    state.history = null;
    state.scroll = null;
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
  });
  afterEach(() => {
    client.clear();
  });

  it.each([
    ["a timed-out read (504)", TIMEOUT, TIMEOUT.result],
    [
      "a backend error (400)",
      {
        status: false,
        result: "Error retrieving trace session.",
        statusCode: 400,
      },
      "Error retrieving trace session.",
    ],
    [
      "a network failure",
      { message: "Network Error", transportCode: "ERR_NETWORK" },
      "Network Error",
    ],
    [
      "an error body with no readable message",
      { status: false, result: { detail: ["bad"] }, statusCode: 400 },
      "Please try again.",
    ],
  ])(
    "shows a retryable error, not an empty session, after %s",
    async (_name, failure, detail) => {
      state.get.mockRejectedValueOnce(failure);
      renderDrawer();

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Failed to load session");
      expect(alert).toHaveTextContent(detail);
      expect(screen.queryByTestId("session-history")).not.toBeInTheDocument();

      let resolveRetry;
      state.get.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveRetry = resolve;
          }),
      );
      fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));

      // While the retry is in flight the drawer shows its loading state.
      await waitFor(() =>
        expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
      );
      expect(state.history.loading).toBe(true);
      expect(state.get).toHaveBeenCalledTimes(2);

      await act(async () => resolveRetry(page(["row-after-retry"])));
      await waitFor(() =>
        expect(screen.getByTestId("session-history")).toHaveTextContent(
          "row-after-retry",
        ),
      );
      expect(state.history.loading).toBe(false);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it("shows the error when Next moves to a session whose read fails", async () => {
    state.get.mockImplementation((url) =>
      url === "/sessions/session-1/query/"
        ? Promise.resolve(page(["first-row"], { next_session_id: "session-2" }))
        : Promise.reject(TIMEOUT),
    );
    renderDrawer();
    await waitFor(() =>
      expect(screen.getByTestId("session-history")).toHaveTextContent(
        "first-row",
      ),
    );

    await act(async () => state.header.handleNextTrace());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Failed to load session");
    expect(alert).toHaveTextContent(TIMEOUT.result);
    expect(screen.queryByTestId("session-history")).not.toBeInTheDocument();

    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(state.get).toHaveBeenCalledTimes(3));
    expect(state.get.mock.calls[2][0]).toBe("/sessions/session-2/query/");
  });

  it("keeps loaded rows, with no error state, when a later page fails", async () => {
    state.get
      .mockResolvedValueOnce(page(["first-row"], {}, true))
      .mockRejectedValueOnce(TIMEOUT);
    renderDrawer();
    await waitFor(() =>
      expect(screen.getByTestId("session-history")).toHaveTextContent(
        "first-row",
      ),
    );

    await act(async () => state.scroll());
    await waitFor(() =>
      expect(client.getQueryCache().getAll()[0].state.status).toBe("error"),
    );
    await waitFor(() => expect(state.history.isFetchingNextPage).toBe(false));

    expect(state.get).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("session-history")).toHaveTextContent(
      "first-row",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
