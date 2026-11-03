import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock only the axios default instance; keep the real `endpoints` so the URL
// is the genuine one.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { endpoints } = axiosMod;
const { default: useKpis, kpisQueryOptions } = await import("../useKpis");
const { extractKpis } = await import("src/sections/test-detail/common");
const { useRunsSummary } = await import(
  "src/sections/simulate/environments/workspace/runs/summary/useRunsSummary"
);

const BODY = { agent_type: "text", total_calls: 16, completed_calls: 12, task_completion: 0.75 };

// One real run, carrying no inline `scores` — a mock run's inline scores would
// skip the fetch and the summary would never observe the key at all.
const RUN = { id: "ex-1", executionId: "ex-1", ordinal: 1, label: "Run 1", total: 4, passed: 3 };
// Held outside the render so the runs array keeps its identity across renders.
const ENV_STATE = { runs: [RUN] };

const makeWrapper = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // The summary reads the `?mockRuns=1` switch off the URL, so its hook needs
  // a router around it.
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return { queryClient, Wrapper };
};

beforeEach(() => {
  axios.get.mockReset();
  axios.get.mockResolvedValue({ data: BODY, status: 200 });
});

// Two observers write this key: `useKpis` and the environments workspace's
// `useRunsSummary`, which needs the options rather than the hook because
// `useQueries` cannot call one. Written out twice they drifted — and what is
// cached is the response BODY, so an observer caching the raw AxiosResponse
// hands whichever one mounts second the wrong shape off the same entry.
describe("kpisQueryOptions", () => {
  it("is the one definition of the key, the fetch and the freshness", () => {
    const options = kpisQueryOptions("ex-1");
    expect(options.queryKey).toEqual(["test-execution-detail", "KPIS", "ex-1"]);
    expect(options.staleTime).toBe(1000 * 60 * 5);
  });

  it("caches the response body, not the AxiosResponse", async () => {
    const body = await kpisQueryOptions("ex-1").queryFn();
    expect(axios.get).toHaveBeenCalledWith(endpoints.testExecutions.kpis("ex-1"));
    expect(body).toEqual(BODY);
    expect(body).not.toHaveProperty("data");
    expect(body).not.toHaveProperty("status");
  });
});

describe("useKpis", () => {
  it("writes the body under the shared key", async () => {
    const { queryClient, Wrapper } = makeWrapper();
    const { result } = renderHook(() => useKpis("ex-1"), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(BODY);
    expect(queryClient.getQueryData(kpisQueryOptions("ex-1").queryKey)).toEqual(BODY);
  });

  it("stays disabled without an execution id", () => {
    const { Wrapper } = makeWrapper();
    renderHook(() => useKpis(undefined), { wrapper: Wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });

  // The real summary hook, not a hand-built options literal: it is the hook
  // that used to restate the key, the fetch and the freshness, so it is the
  // hook that has to be observed writing the entry this one then reads.
  //
  // The summary is mounted FIRST and alone, so the fetch that fills the cache
  // is unambiguously its own — mounted together, the two observers dedupe
  // onto whichever registered first and a restated fetch would never run.
  // Its run carries no inline scores, so it genuinely fetches.
  it("shares one cache entry, and one fetch, with the runs summary", async () => {
    const { queryClient, Wrapper } = makeWrapper();
    const summary = renderHook(() => useRunsSummary(undefined, ENV_STATE), { wrapper: Wrapper });

    // What the summary cached is the body its own `select` is written
    // against — an AxiosResponse would score this row on the wrong shape.
    await waitFor(() =>
      expect(summary.result.current.rows[0].scores).toEqual(
        extractKpis(BODY, BODY.agent_type).evalMetrics,
      ),
    );
    expect(summary.result.current.rows[0].scores).toMatchObject({ task_completion: 0.75 });
    expect(axios.get).toHaveBeenCalledWith(endpoints.testExecutions.kpis("ex-1"));

    // The summary's own observer resolved its freshness from the factory,
    // not a call-site override — the third field `kpisQueryOptions` exists
    // to own, pinned here rather than only by the sibling case above.
    expect(
      queryClient.getQueryCache().find({ queryKey: kpisQueryOptions("ex-1").queryKey })
        .observers[0].options.staleTime,
    ).toBe(1000 * 60 * 5);

    // The detail hook, on the same client, reads that one entry: the same
    // key, still fresh, so no second request and the body it expects.
    const kpis = renderHook(() => useKpis(RUN.executionId), { wrapper: Wrapper });
    await waitFor(() => expect(kpis.result.current.isSuccess).toBe(true));
    expect(kpis.result.current.data).toEqual(BODY);
    expect(axios.get).toHaveBeenCalledTimes(1);
  });
});
