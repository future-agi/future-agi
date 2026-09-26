import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import ObserveHeader from "../ObserveHeader";
import {
  OBSERVE_LIST_REFRESH_EVENT,
  OBSERVE_PAGE_CHANGED_EVENT,
} from "../observeEvents";

const h = vi.hoisted(() => ({
  invalidateQueries: vi.fn(),
  useMutation: vi.fn(),
  getStorage: vi.fn(() => false),
  setStorage: vi.fn(),
  observeId: "project-1",
  pathname: "/dashboard/observe/project-1/llm-tracing",
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: h.useMutation,
  useQueryClient: () => ({ invalidateQueries: h.invalidateQueries }),
}));

vi.mock("react-router", () => ({
  useNavigate: () => vi.fn(),
  useParams: () => ({ observeId: h.observeId }),
  useLocation: () => ({ pathname: h.pathname }),
}));

vi.mock("src/hooks/use-local-storage", () => ({
  getStorage: h.getStorage,
  setStorage: h.setStorage,
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: (_key, initial) => [initial, vi.fn()],
}));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: null }),
}));
vi.mock("../LLMTracing/common", () => ({
  useProjectList: () => ({ data: [], isLoading: false }),
  DOC_LINKS: {
    llmTracing: "https://example.test/tracing",
    sessions: "https://example.test/sessions",
    users: "https://example.test/users",
  },
}));
vi.mock("../LLMTracing/states", () => ({
  resetTraceGridStore: vi.fn(),
  resetSpanGridStore: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { project: {} },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid={icon} />,
}));
vi.mock("src/components/tooltip/CustomTooltip", () => {
  const MockTooltip = ({ children }) => children;
  MockTooltip.propTypes = { children: PropTypes.node };
  return { default: MockTooltip };
});
vi.mock("../SharedComponents", () => {
  const MockObserveIconButton = ({ children, ...props }) => (
    <button type="button" {...props}>
      {children}
    </button>
  );
  MockObserveIconButton.propTypes = { children: PropTypes.node };
  return { ObserveIconButton: MockObserveIconButton };
});
vi.mock("src/components/share-dialog", () => ({ ShareDialog: () => null }));
vi.mock("src/sections/project/TagEditor", () => ({ default: () => null }));
vi.mock("../../project-detail/ConfigureProject", () => ({
  default: () => null,
}));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));

const renderHeader = (refreshData = vi.fn()) => {
  render(<ObserveHeader text="LLM Tracing" refreshData={refreshData} />);
  return refreshData;
};

describe("ObserveHeader exact aggregation refresh state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.getStorage.mockReturnValue(false);
    h.observeId = "project-1";
    h.pathname = "/dashboard/observe/project-1/llm-tracing";
  });

  afterEach(() => vi.useRealTimers());

  it("shows last updated only after an exact completion event", () => {
    renderHeader();
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-completed", {
          detail: {
            observeId: "project-1",
            queryCompletedAt: "2026-08-03T12:34:00",
          },
        }),
      );
    });

    expect(
      screen.getByText(/Last updated on 03\/08\/2026/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/12:34 pm/i)).toBeInTheDocument();
  });

  it("scopes completion events to the current route and resets on navigation", () => {
    const view = render(
      <ObserveHeader text="LLM Tracing" refreshData={vi.fn()} />,
    );

    act(() => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-completed", {
          detail: {
            observeId: "project-2",
            queryCompletedAt: "2026-08-04T12:34:00Z",
          },
        }),
      );
    });
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-completed", {
          detail: {
            observeId: "project-1",
            queryCompletedAt: "2026-08-03T12:34:00Z",
          },
        }),
      );
    });
    expect(screen.getByText(/Last updated on/i)).toBeInTheDocument();

    h.observeId = "project-2";
    h.pathname = "/dashboard/observe/project-2/llm-tracing";
    view.rerender(<ObserveHeader text="LLM Tracing" refreshData={vi.fn()} />);
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();
  });

  it("uses explicit reload for aggregation and does not stamp request time", () => {
    const refreshData = renderHeader();
    const aggregationRefresh = vi.fn();
    window.addEventListener("observe-refresh", aggregationRefresh, {
      once: true,
    });

    fireEvent.click(screen.getByTestId("mdi:refresh").closest("button"));

    expect(refreshData).toHaveBeenCalledWith({ includeAggregations: false });
    expect(aggregationRefresh).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();
  });

  it("keeps reload disabled until every exact aggregation poll settles", () => {
    renderHeader();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-refresh-state", {
          detail: {
            observeId: "project-1",
            sourceId: "primary",
            refreshing: true,
          },
        }),
      );
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-refresh-state", {
          detail: {
            observeId: "project-1",
            sourceId: "compare",
            refreshing: true,
          },
        }),
      );
    });

    expect(
      screen.getByRole("button", { name: "Refreshing data" }),
    ).toBeDisabled();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-refresh-state", {
          detail: {
            observeId: "project-1",
            sourceId: "primary",
            refreshing: false,
          },
        }),
      );
    });
    expect(
      screen.getByRole("button", { name: "Refreshing data" }),
    ).toBeDisabled();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-refresh-state", {
          detail: {
            observeId: "project-1",
            sourceId: "compare",
            refreshing: false,
          },
        }),
      );
    });
    expect(screen.getByRole("button", { name: "Reload data" })).toBeEnabled();
  });

  it("keeps 10-second auto refresh scoped to list data", () => {
    vi.useFakeTimers();
    h.getStorage.mockReturnValue(true);
    const aggregationRefresh = vi.fn();
    const listRefresh = vi.fn();
    window.addEventListener("observe-refresh", aggregationRefresh, {
      once: true,
    });
    window.addEventListener(OBSERVE_LIST_REFRESH_EVENT, listRefresh, {
      once: true,
    });
    const refreshData = renderHeader();

    act(() => vi.advanceTimersByTime(10_000));

    expect(refreshData).not.toHaveBeenCalled();
    expect(listRefresh).toHaveBeenCalledOnce();
    expect(aggregationRefresh).not.toHaveBeenCalled();
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();
  });

  it("turns auto refresh off when an explicit paginator leaves page one", () => {
    vi.useFakeTimers();
    h.getStorage.mockReturnValue(true);
    const listRefresh = vi.fn();
    window.addEventListener(OBSERVE_LIST_REFRESH_EVENT, listRefresh);
    renderHeader();

    act(() => {
      window.dispatchEvent(
        new CustomEvent(OBSERVE_PAGE_CHANGED_EVENT, { detail: { page: 2 } }),
      );
    });
    expect(h.setStorage).toHaveBeenCalledWith("autoRefresh", false);

    act(() => vi.advanceTimersByTime(10_000));
    expect(listRefresh).not.toHaveBeenCalled();
    window.removeEventListener(OBSERVE_LIST_REFRESH_EVENT, listRefresh);
  });

  it("keeps the page-change shutoff armed while auto refresh is off", () => {
    h.getStorage.mockReturnValue(false);
    renderHeader();

    act(() => {
      window.dispatchEvent(
        new CustomEvent(OBSERVE_PAGE_CHANGED_EVENT, { detail: { page: 2 } }),
      );
    });

    expect(h.setStorage).toHaveBeenCalledWith("autoRefresh", false);
  });

  it.each([
    ["LLM Tracing", "/dashboard/observe/project-1/llm-tracing"],
    ["Sessions", "/dashboard/observe/project-1/sessions"],
  ])(
    "keeps the %s exact CSV export unavailable without creating a request path",
    (text, pathname) => {
      h.pathname = pathname;
      render(<ObserveHeader text={text} refreshData={vi.fn()} />);

      const exportButton = screen.getByRole("button", {
        name: "Exact CSV export is temporarily unavailable",
      });
      expect(exportButton).toBeDisabled();

      fireEvent.click(exportButton);
      expect(h.useMutation).not.toHaveBeenCalled();
    },
  );

  it("keeps the Users export control hidden", () => {
    h.pathname = "/dashboard/observe/project-1/users";
    render(<ObserveHeader text="Users" refreshData={vi.fn()} />);

    expect(
      screen.queryByRole("button", {
        name: "Exact CSV export is temporarily unavailable",
      }),
    ).not.toBeInTheDocument();
  });
});
