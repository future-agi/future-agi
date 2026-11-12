import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import ErrorFeedView from "../ErrorFeedView";
import { useErrorFeedStore } from "../store";

const LIST_URL = "/tracer/feed/issues/";
const mocks = vi.hoisted(() => ({ get: vi.fn() }));

// Keep the real list hooks, store, filters, table and pagination footer. Only
// the transport and unrelated widgets are replaced.
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, patch: vi.fn() },
  endpoints: { errorFeed: { list: "/tracer/feed/issues/" } },
}));
vi.mock("src/api/project/observe-project-list", () => ({
  fetchAllObserveProjects: () => Promise.resolve([]),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/filter", () => ({ FilterButton: () => null }));
vi.mock("../components/ErrorTrendSparkline", () => ({ default: () => null }));

const DEFAULT_LIST_STATE = {
  searchQuery: "",
  selectedProject: "",
  selectedEnvironment: "",
  selectedStatus: "",
  selectedSeverity: "",
  selectedErrorType: "",
  selectedFixLayer: "",
  selectedSource: "",
  timeRange: "7",
  sortBy: "lastSeen",
  sortDir: "desc",
  page: 0,
  pageSize: 25,
};

const TOTAL = 42;
// A search the list has no match for.
const NO_MATCH = "zzz-no-match";
const issue = (n, name = `Issue ${n}`) => ({
  cluster_id: `E-${n}`,
  error: { name, type: "Tool Selection Error" },
  source: "scanner",
  severity: "high",
  status: "escalating",
  trace_count: 7,
  users_affected: 3,
  fix_layer: "prompt",
  trends: [],
  last_seen: "2026-11-01T00:00:00Z",
});
const listResult = (params) => {
  if (params.search === NO_MATCH) return { data: [], total: 0 };
  // Any Fix layer filter matches nothing here, so it empties the list.
  if (params.fix_layer) return { data: [], total: 0 };
  if (params.search) {
    return { data: [issue(900, "Tool call timeout")], total: 1 };
  }
  const offset = params.offset ?? 0;
  const count = Math.max(0, Math.min(params.limit, TOTAL - offset));
  return {
    data: Array.from({ length: count }, (_, i) => issue(offset + i + 1)),
    total: TOTAL,
  };
};

let queryClient;
let holdRequests;
let held;

const listCalls = () =>
  mocks.get.mock.calls
    .filter(([url]) => url === LIST_URL)
    .map(([, config]) => config.params);
const searches = () =>
  listCalls()
    .map((params) => params.search)
    .filter(Boolean);
const releaseHeld = () =>
  act(async () => {
    held.splice(0).forEach((release) => release());
  });
// The count badge sits next to the page title.
const header = () =>
  within(screen.getByRole("heading", { name: "Error Feed" }).parentElement);

const renderFeed = () =>
  render(
    <QueryClientProvider client={queryClient}>
      <ErrorFeedView />
    </QueryClientProvider>,
  );

beforeEach(() => {
  holdRequests = false;
  held = [];
  mocks.get.mockReset().mockImplementation((url, config = {}) => {
    if (url !== LIST_URL) {
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    }
    const reply = { data: { result: listResult(config.params) } };
    if (!holdRequests) return Promise.resolve(reply);
    return new Promise((resolve) => held.push(() => resolve(reply)));
  });
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  useErrorFeedStore.setState(DEFAULT_LIST_STATE);
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  useErrorFeedStore.setState(DEFAULT_LIST_STATE);
});

describe("W10-ERROR-FEED-U08: Error Feed keeps its rows while it reloads", () => {
  it("keeps the rows, count and pagination while typing a search, then sends one request", async () => {
    renderFeed();
    expect(await screen.findByText("Issue 1")).toBeInTheDocument();
    expect(header().getByText("42")).toBeInTheDocument();
    expect(listCalls()).toHaveLength(1);

    holdRequests = true;
    const search = screen.getByPlaceholderText("Search errors");
    // Type "timeout" one letter at a time, with no pause between letters.
    const word = "timeout";
    for (let i = 1; i <= word.length; i += 1) {
      fireEvent.change(search, { target: { value: word.slice(0, i) } });
    }

    expect(search).toHaveValue("timeout");
    expect(screen.getByText("Issue 1")).toBeInTheDocument();
    expect(header().getByText("42")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Next page" }),
    ).toBeInTheDocument();

    // Once typing pauses, one request goes out for the whole word, and the
    // current rows stay on screen until it answers.
    await waitFor(() => expect(searches()).toEqual(["timeout"]), {
      timeout: 2000,
    });
    expect(screen.getByText("Issue 1")).toBeInTheDocument();
    expect(header().getByText("42")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading page");

    await releaseHeld();
    expect(await screen.findByText("Tool call timeout")).toBeInTheDocument();
    expect(screen.queryByText("Issue 1")).not.toBeInTheDocument();
    expect(await header().findByText("1")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(searches()).toEqual(["timeout"]);
  });

  it("keeps the current page on screen while the next page or a new sort loads", async () => {
    renderFeed();
    expect(await screen.findByText("Issue 1")).toBeInTheDocument();

    holdRequests = true;
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(listCalls().at(-1)).toMatchObject({ offset: 25 });
    expect(screen.getByText("Issue 1")).toBeInTheDocument();
    expect(header().getByText("42")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading page");

    await releaseHeld();
    expect(await screen.findByText("Issue 26")).toBeInTheDocument();
    expect(screen.queryByText("Issue 1")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Severity"));
    expect(listCalls().at(-1)).toMatchObject({
      sort_by: "severity",
      offset: 25,
    });
    expect(screen.getByText("Issue 26")).toBeInTheDocument();
    expect(header().getByText("42")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading page");

    await releaseHeld();
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Issue 26")).toBeInTheDocument();
  });

  it("empties the search box when Clear filters clears the search", async () => {
    renderFeed();
    expect(await screen.findByText("Issue 1")).toBeInTheDocument();

    const search = screen.getByPlaceholderText("Search errors");
    fireEvent.change(search, { target: { value: NO_MATCH } });
    expect(
      await screen.findByText(
        "No errors match your filters",
        {},
        { timeout: 2000 },
      ),
    ).toBeInTheDocument();

    // The empty state's Clear filters clears the stored search. The box must
    // follow, or it shows a search the list is no longer using.
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(search).toHaveValue("");
    expect(useErrorFeedStore.getState().searchQuery).toBe("");
    expect(await screen.findByText("Issue 1")).toBeInTheDocument();
    expect(searches()).toEqual([NO_MATCH]);
  });

  it("drops a search still being typed when Clear filters clears another filter (OBS-SW-174)", async () => {
    // Another filter empties the list while the stored search is empty, so
    // clearing changes nothing about the search itself.
    useErrorFeedStore.setState({ selectedFixLayer: "Guardrails" });
    renderFeed();
    expect(
      await screen.findByText("No errors match your filters"),
    ).toBeInTheDocument();

    // Type a search, then clear before the box's typing pause sends it.
    const search = screen.getByPlaceholderText("Search errors");
    fireEvent.change(search, { target: { value: "timeout" } });
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(search).toHaveValue("");
    expect(useErrorFeedStore.getState().selectedFixLayer).toBe("");
    expect(await screen.findByText("Issue 1")).toBeInTheDocument();

    // Wait out the typing pause: the cleared search must not come back and
    // filter the list again.
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 500);
      });
    });
    expect(search).toHaveValue("");
    expect(useErrorFeedStore.getState().searchQuery).toBe("");
    expect(searches()).toEqual([]);
    expect(screen.getByText("Issue 1")).toBeInTheDocument();
  });
});
