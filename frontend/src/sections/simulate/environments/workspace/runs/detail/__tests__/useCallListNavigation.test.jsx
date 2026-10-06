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
// Two pages. `results` is the server order; the table shows each page's rows
// bucketed into groups, so what's on screen differs:
//   page 1 results c1 c2 c3 → on screen [c2] [c1 c3]
//   page 2 results c4 c5    → on screen [c5] [c4]
// Prev/next follow the screen: c2 c1 c3 | c5 c4.
const PAGES = {
  1: { results: ["c1", "c2", "c3"], groups: [["c2"], ["c1", "c3"]] },
  2: { results: ["c4", "c5"], groups: [["c5"], ["c4"]] },
};
const pageData = (page) => ({
  execution: { status: "completed" },
  count: 5,
  page,
  total_pages: 2,
  results: (PAGES[page]?.results ?? []).map(row),
  groups: (PAGES[page]?.groups ?? []).map((ids, i) => ({
    key: `g${i}`,
    label: `Group ${i}`,
    result_ids: ids,
    total: ids.length,
  })),
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
  it("steps in the order the rows are on screen, not the results order", async () => {
    // c2 is first on screen although it's second in results.
    const first = setup({ callId: "c2" });
    await waitFor(() => expect(first.result.current.hasNext).toBe(true));
    expect(first.result.current.hasPrev).toBe(false);
    act(() => first.result.current.onNext());
    expect(first.onStep).toHaveBeenCalledWith({
      task: expect.objectContaining({ id: "c1", scenario: "Scenario c1" }),
      source: "table",
      page: 1,
    });
    first.unmount();

    // c1 is first in results but second on screen: prev goes up to c2.
    const second = setup({ callId: "c1" });
    await waitFor(() => expect(second.result.current.hasPrev).toBe(true));
    act(() => second.result.current.onPrev());
    expect(second.onStep).toHaveBeenCalledWith(
      expect.objectContaining({ task: expect.objectContaining({ id: "c2" }) }),
    );
  });

  it("visits a call once when it sits in several groups, and still reaches the rest", async () => {
    // Sub-goal groups overlap: a is in both. On screen [a b] [a c] reads a b c.
    axios.get.mockImplementation((_url, { params }) =>
      Promise.resolve({
        data: {
          ...pageData(params.page),
          count: 3,
          total_pages: 1,
          results: ["a", "b", "c"].map(row),
          groups: [
            { key: "g0", label: "Group 0", result_ids: ["a", "b"], total: 2 },
            { key: "g1", label: "Group 1", result_ids: ["a", "c"], total: 2 },
          ],
        },
      }),
    );
    const { result, onStep } = setup({ callId: "b" });
    await waitFor(() => expect(result.current.hasNext).toBe(true));
    expect(result.current.hasPrev).toBe(true);

    act(() => result.current.onNext());
    expect(onStep).toHaveBeenLastCalledWith(
      expect.objectContaining({ task: expect.objectContaining({ id: "c" }) }),
    );
    onStep.mockClear();
    act(() => result.current.onPrev());
    expect(onStep).toHaveBeenLastCalledWith(
      expect.objectContaining({ task: expect.objectContaining({ id: "a" }) }),
    );
  });

  it("walks a to b and back to a when a sits in two groups", async () => {
    axios.get.mockImplementation((_url, { params }) =>
      Promise.resolve({
        data: {
          ...pageData(params.page),
          count: 3,
          total_pages: 1,
          results: ["a", "b", "c"].map(row),
          groups: [
            { key: "g0", label: "Group 0", result_ids: ["a", "b"], total: 2 },
            { key: "g1", label: "Group 1", result_ids: ["a", "c"], total: 2 },
          ],
        },
      }),
    );
    const { result, rerender, onStep } = setup({ callId: "a" });
    await waitFor(() => expect(result.current.hasNext).toBe(true));
    expect(result.current.hasPrev).toBe(false);

    act(() => result.current.onNext());
    expect(onStep).toHaveBeenLastCalledWith(
      expect.objectContaining({ task: expect.objectContaining({ id: "b" }) }),
    );

    rerender(open("b"));
    expect(result.current.hasPrev).toBe(true);
    act(() => result.current.onPrev());
    expect(onStep).toHaveBeenLastCalledWith(
      expect.objectContaining({ task: expect.objectContaining({ id: "a" }) }),
    );

    rerender(open("a"));
    expect(result.current.hasPrev).toBe(false);
    act(() => result.current.onNext());
    expect(onStep).toHaveBeenLastCalledWith(
      expect.objectContaining({ task: expect.objectContaining({ id: "b" }) }),
    );
    expect(onStep).toHaveBeenCalledTimes(3);
  });

  // Pages whose groups overlap, given as { page: { results, groups } }.
  const mockPages = (spec) =>
    axios.get.mockImplementation((_url, { params }) => {
      const { results, groups } = spec[params.page];
      return Promise.resolve({
        data: {
          ...pageData(params.page),
          count: Object.values(spec).flatMap((s) => s.results).length,
          total_pages: Object.keys(spec).length,
          results: results.map(row),
          groups: groups.map((ids, i) => ({
            key: `g${i}`,
            label: `Group ${i}`,
            result_ids: ids,
            total: ids.length,
          })),
        },
      });
    });
  const open = (id, page = 1) => ({
    openCall: { task: { id }, source: "table", page: null },
    tableQuery: tableQuery(page),
  });
  const lastStep = (onStep) => onStep.mock.calls.at(-1)[0];

  it("ends the list at the last distinct call, even when a repeated call sits below it", async () => {
    // On screen [b a] [c a] reads b a c: a repeats after c, but c is the end.
    mockPages({ 1: { results: ["a", "b", "c"], groups: [["b", "a"], ["c", "a"]] } });
    const { result, rerender, onStep } = setup({ callId: "c" });
    await waitFor(() => expect(result.current.hasPrev).toBe(true));
    expect(result.current.hasNext).toBe(false);

    act(() => result.current.onPrev());
    expect(lastStep(onStep).task.id).toBe("a");

    rerender(open("a"));
    act(() => result.current.onNext());
    expect(lastStep(onStep).task.id).toBe("c");
  });

  it("steps through every distinct call once when one sits in three groups", async () => {
    mockPages({
      1: { results: ["a", "b", "c", "d"], groups: [["a", "b"], ["a", "c"], ["a", "d"]] },
    });
    const { result, rerender, onStep } = setup({ callId: "a" });
    await waitFor(() => expect(result.current.hasNext).toBe(true));

    const visited = [];
    for (const id of ["a", "b", "c"]) {
      rerender(open(id));
      act(() => result.current.onNext());
      visited.push(lastStep(onStep).task.id);
    }
    expect(visited).toEqual(["b", "c", "d"]);
    rerender(open("d"));
    expect(result.current.hasNext).toBe(false);
    act(() => result.current.onPrev());
    expect(lastStep(onStep).task.id).toBe("c");
  });

  it("crosses pages by distinct calls when both pages have a repeated call", async () => {
    // page 1 on screen [a c] [b c] reads a c b; page 2 [d e] [d f] reads d e f.
    mockPages({
      1: { results: ["a", "b", "c"], groups: [["a", "c"], ["b", "c"]] },
      2: { results: ["d", "e", "f"], groups: [["d", "e"], ["d", "f"]] },
    });
    const { result, onStep } = setup({ callId: "b" });
    await waitFor(() => expect(result.current.hasNext).toBe(true));
    await act(() => result.current.onNext());
    expect(lastStep(onStep)).toEqual(
      expect.objectContaining({ task: expect.objectContaining({ id: "d" }), page: 2 }),
    );

    const back = setup({ callId: "d", page: 2 });
    await waitFor(() => expect(back.result.current.hasPrev).toBe(true));
    await act(() => back.result.current.onPrev());
    expect(lastStep(back.onStep)).toEqual(
      expect.objectContaining({ task: expect.objectContaining({ id: "b" }), page: 1 }),
    );
  });

  it("crosses from the last row on screen to the next page's first row on screen", async () => {
    const { result, onStep } = setup({ callId: "c3" });
    await waitFor(() => expect(result.current.hasNext).toBe(true));
    // The next page is preloaded as soon as the open call sits on the edge.
    await waitFor(() => expect(pagesRequested()).toContain(2));

    await act(() => result.current.onNext());
    expect(onStep).toHaveBeenCalledWith({
      task: expect.objectContaining({ id: "c5" }),
      source: "table",
      page: 2,
    });
  });

  it("crosses from the first row on screen back to the previous page's last row on screen", async () => {
    const { result, onStep } = setup({ callId: "c5", page: 2 });
    await waitFor(() => expect(result.current.hasPrev).toBe(true));

    await act(() => result.current.onPrev());
    expect(onStep).toHaveBeenCalledWith({
      task: expect.objectContaining({ id: "c3" }),
      source: "table",
      page: 1,
    });
  });

  it("stops at both ends of the list", async () => {
    const first = setup({ callId: "c2" });
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
    const { result } = setup({ callId: "c2", source: "analytics" });
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
    const { result, onStep } = setup({ callId: "c3" });
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
