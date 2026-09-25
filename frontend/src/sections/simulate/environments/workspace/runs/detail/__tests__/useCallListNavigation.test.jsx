import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Only the network is mocked: the hook reads the real calls-list query, so a
// page is "loaded" by answering its request.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});
const enqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => enqueueSnackbar(...args),
}));

const axios = (await import("src/utils/axios")).default;
const { default: useCallListNavigation } = await import(
  "../useCallListNavigation"
);

const row = (id) => ({ id, scenario: `Scenario ${id}`, status: "completed" });
// Two pages of two calls: c1 c2 | c3 c4.
const PAGES = { 1: ["c1", "c2"], 2: ["c3", "c4"] };
const pageData = (page, totalPages = 2) => ({
  execution: { status: "completed" },
  count: 4,
  page,
  total_pages: totalPages,
  results: (PAGES[page] ?? []).map(row),
  groups: [],
  evaluation_columns: [],
});

const tableQuery = (page = 1) => ({
  page,
  limit: 2,
  search: "",
  filters: {},
  groupBy: "goal",
});

const setup = ({ callId, page = 1, source = "table", query, live = false }) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  wrapper.propTypes = { children: PropTypes.node };
  const onStep = vi.fn();
  const openCall = { task: { id: callId }, source, page: null };
  const hook = renderHook(
    (props) =>
      useCallListNavigation({
        executionId: "ex1",
        openCall: props.openCall,
        tableQuery: props.tableQuery,
        live,
        onStep,
      }),
    {
      wrapper,
      initialProps: {
        openCall,
        tableQuery: query === undefined ? tableQuery(page) : query,
      },
    },
  );
  return { ...hook, onStep };
};

const pagesRequested = () =>
  axios.get.mock.calls.map(([, config]) => config.params.page);

beforeEach(() => {
  axios.get.mockReset();
  enqueueSnackbar.mockReset();
  axios.get.mockImplementation((_url, { params }) =>
    Promise.resolve({ data: pageData(params.page) }),
  );
});

describe("useCallListNavigation", () => {
  it("steps within the page to the full neighbour row", async () => {
    const { result, onStep } = setup({ callId: "c1" });
    await waitFor(() => expect(result.current.hasNext).toBe(true));
    expect(result.current.hasPrev).toBe(false);

    act(() => result.current.onNext());
    expect(onStep).toHaveBeenCalledWith({
      task: expect.objectContaining({ id: "c2", scenario: "Scenario c2" }),
      source: "table",
      page: 1,
    });
  });

  it("crosses from the last row of a page to the first row of the next", async () => {
    const { result, onStep } = setup({ callId: "c2" });
    await waitFor(() => expect(result.current.hasNext).toBe(true));
    // The next page is preloaded as soon as the open call sits on the edge.
    await waitFor(() => expect(pagesRequested()).toContain(2));

    await act(() => result.current.onNext());
    expect(onStep).toHaveBeenCalledWith({
      task: expect.objectContaining({ id: "c3" }),
      source: "table",
      page: 2,
    });
  });

  it("crosses from the first row of a page back to the last row of the previous", async () => {
    const { result, onStep } = setup({ callId: "c3", page: 2 });
    await waitFor(() => expect(result.current.hasPrev).toBe(true));

    await act(() => result.current.onPrev());
    expect(onStep).toHaveBeenCalledWith({
      task: expect.objectContaining({ id: "c2" }),
      source: "table",
      page: 1,
    });
  });

  it("stops at both ends of the list", async () => {
    const first = setup({ callId: "c1" });
    await waitFor(() => expect(first.result.current.hasNext).toBe(true));
    expect(first.result.current.hasPrev).toBe(false);
    first.unmount();

    const last = setup({ callId: "c4", page: 2 });
    await waitFor(() => expect(last.result.current.hasPrev).toBe(true));
    expect(last.result.current.hasNext).toBe(false);
  });

  it("disables both arrows when the call isn't on the table's page", async () => {
    const { result } = setup({ callId: "c9" });
    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    expect(result.current.hasPrev).toBe(false);
    expect(result.current.hasNext).toBe(false);
  });

  it("gives a call opened from Analytics no prev/next and reads nothing", () => {
    const { result } = setup({ callId: "c1", source: "analytics" });
    expect(result.current.hasPrev).toBe(false);
    expect(result.current.hasNext).toBe(false);
    expect(axios.get).not.toHaveBeenCalled();
  });

  it("disables both arrows once the table is gone", () => {
    const { result } = setup({ callId: "c1", query: null });
    expect(result.current.hasNext).toBe(false);
    expect(axios.get).not.toHaveBeenCalled();
  });

  it("stays on the call and says so when the next page can't load", async () => {
    axios.get.mockImplementation((_url, { params }) =>
      params.page === 2
        ? Promise.reject(new Error("boom"))
        : Promise.resolve({ data: pageData(params.page) }),
    );
    const { result, onStep } = setup({ callId: "c2" });
    await waitFor(() => expect(result.current.hasNext).toBe(true));

    await act(() => result.current.onNext());
    expect(onStep).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Couldn't load the next calls",
      { variant: "error" },
    );
    expect(result.current.hasNext).toBe(true);
  });
});
