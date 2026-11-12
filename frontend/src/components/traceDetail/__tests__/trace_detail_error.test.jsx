import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "src/utils/test-utils";
import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

const mocks = vi.hoisted(() => ({
  ownTrace: vi.fn(),
  sharedTrace: vi.fn(),
  refetch: vi.fn(),
  sharedRefetch: vi.fn(),
}));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (...args) => mocks.ownTrace(...args),
}));
vi.mock("src/api/shared-links", async (importOriginal) => ({
  ...(await importOriginal()),
  useSharedIssueTrace: (...args) => mocks.sharedTrace(...args),
}));
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: undefined }),
}));
vi.mock("../DrawerHeader", () => ({ default: () => null }));
vi.mock("../DrawerToolbar", () => ({ default: () => null }));
vi.mock("../SpanDetailPane", () => ({ default: () => null }));

const failedTrace = (statusCode = 503, extra = {}) => ({
  isLoading: false,
  isError: true,
  // Axios exposes the generic wire error, not the internal span-limit diagnostic.
  error: { statusCode, result: "Service unavailable" },
  refetch: mocks.refetch,
  ...extra,
});

function renderDrawer(props = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const drawer = (nextProps) => (
    <QueryClientProvider client={client}>
      <TraceDetailDrawerV2
        open
        onClose={vi.fn()}
        traceId="trace-1"
        projectId="project-1"
        {...nextProps}
      />
    </QueryClientProvider>
  );
  const result = render(drawer(props));
  return {
    ...result,
    rerenderDrawer: (nextProps = props) => result.rerender(drawer(nextProps)),
  };
}

describe("trace detail failure states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    mocks.ownTrace.mockReturnValue(failedTrace());
    mocks.sharedTrace.mockReturnValue({ isLoading: false });
  });

  it("shows an actionable 503 and retries without claiming the trace is empty or diagnosing a limit", () => {
    renderDrawer();
    expect(screen.getByRole("alert")).toHaveTextContent("Unable to load trace");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "check service availability and trace size limits",
    );
    expect(
      screen.queryByText("No spans in this trace"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/span_limit_exceeded|1000|exceeds/i),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(mocks.refetch).toHaveBeenCalledExactlyOnceWith();
  });

  it.each([
    [401, "Sign in again"],
    [403, "Ask your workspace administrator for access"],
  ])(
    "preserves access denial for %s instead of offering a service retry",
    (statusCode, guidance) => {
      mocks.ownTrace.mockReturnValue(failedTrace(statusCode));
      renderDrawer();
      expect(screen.getByRole("alert")).toHaveTextContent(guidance);
      expect(
        screen.queryByRole("button", { name: "Retry" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText("No spans in this trace"),
      ).not.toBeInTheDocument();
    },
  );

  it.each([
    [404, "Return to the trace list"],
    [undefined, "The trace could not be loaded"],
  ])(
    "shows safe recovery guidance for %s without exposing internal details",
    (statusCode, guidance) => {
      mocks.ownTrace.mockReturnValue(
        failedTrace(statusCode, {
          error: { statusCode, message: "SELECT secret FROM internal_table" },
        }),
      );
      renderDrawer();
      expect(screen.getByRole("alert")).toHaveTextContent(guidance);
      expect(screen.queryByText(/SELECT secret/)).not.toBeInTheDocument();
    },
  );

  it("hides stale spans on a failed refresh and disables duplicate retry while fetching", () => {
    mocks.ownTrace.mockReturnValue(
      failedTrace(503, {
        isFetching: true,
        data: {
          observation_spans: [
            {
              observation_span: { id: "stale", name: "Stale span" },
              children: [],
            },
          ],
          summary: { total_cost: 987.65 },
        },
      }),
    );
    renderDrawer();
    expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
    expect(screen.queryByText("Stale span")).not.toBeInTheDocument();
    expect(screen.queryByText("$987.65")).not.toBeInTheDocument();
    expect(
      screen.queryByText("No spans in this trace"),
    ).not.toBeInTheDocument();
  });

  it("clears the old error when another trace loads, and only then shows a legitimate empty trace", () => {
    const { rerenderDrawer } = renderDrawer();
    mocks.ownTrace.mockReturnValue({
      isLoading: true,
      isError: false,
      refetch: mocks.refetch,
    });
    rerenderDrawer({ traceId: "trace-2" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(
      screen.queryByText("No spans in this trace"),
    ).not.toBeInTheDocument();

    mocks.ownTrace.mockReturnValue({
      isLoading: false,
      isError: false,
      data: { observation_spans: [] },
      refetch: mocks.refetch,
    });
    rerenderDrawer({ traceId: "trace-2" });
    expect(mocks.ownTrace).toHaveBeenLastCalledWith("trace-2", {
      projectId: "project-1",
      testExecutionId: undefined,
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("No spans in this trace")).toBeInTheDocument();
  });

  it("retries the active share-token query rather than the signed-in query", () => {
    mocks.sharedTrace.mockReturnValue(
      failedTrace(503, { refetch: mocks.sharedRefetch }),
    );
    renderDrawer({ sharedToken: "synthetic-share", readOnly: true });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(mocks.sharedRefetch).toHaveBeenCalledOnce();
    expect(mocks.refetch).not.toHaveBeenCalled();
  });
});
