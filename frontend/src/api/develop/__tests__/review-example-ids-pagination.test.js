import { beforeEach, describe, expect, it, vi } from "vitest";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    develop: {
      datasetRuns: {
        results: (id) => `/model-hub/datasets/${encodeURIComponent(id)}/results/`,
      },
    },
  },
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  annotationQueueEndpoints: {},
  annotationQueueKeys: { all: ["annotation-queues"] },
}));

import { fetchResultExampleIds } from "../review";

// Exercise the real bounded Results helper; mock only the HTTP boundary and
// unrelated annotation-queue imports. Dataset order need not sort UUIDs.
const exampleIds = Array.from(
  { length: 1001 },
  (_, index) => `00000000-0000-4000-8000-${String(1001 - index).padStart(12, "0")}`,
);
const firstIds = exampleIds.slice(0, 1000);
const lastId = exampleIds[1000];
const view = { runs: ["run-b", "run-a"], baseline: "run-a", sort: "order" };
const url = "/model-hub/datasets/dataset-1/results/";

function pageResponse({
  ids = firstIds,
  page = 0,
  total = 1001,
  hasMore = true,
  next = 1,
  selectionId = "selection-1",
  complete = true,
} = {}) {
  return {
    data: {
      status: true,
      result: {
        total,
        example_ids: ids,
        summary: {},
        pagination: { page, page_size: 1000, total, has_more: hasMore, next },
        scope: { population: total, complete, selection_id: selectionId },
      },
    },
  };
}

const finalPage = (overrides = {}) =>
  pageResponse({ ids: [lastId], page: 1, hasMore: false, next: null, ...overrides });

beforeEach(() => {
  get.mockReset();
});

describe("review example ID pagination", () => {
  it.each([
    { label: "Dataset order", filters: [], sort: "order", search: "" },
    {
      label: "a filtered, sorted search",
      filters: [{ type: "split", values: ["test"] }],
      sort: "-input",
      search: "candidate",
    },
  ])("resolves all 1001 IDs in $label, including selected IDs on page 1", async ({
    filters,
    sort,
    search,
  }) => {
    get.mockResolvedValueOnce(pageResponse()).mockResolvedValueOnce(finalPage());

    const result = await fetchResultExampleIds("dataset-1", {
      ...view,
      filters,
      sort,
      search,
    });

    expect(result).toEqual({ total: 1001, example_ids: exampleIds });
    // Same intersection as StartReviewDialog: preserve dataset order, not
    // selection order, and retain the selected example from the second page.
    const selected = new Set([lastId, firstIds[2]]);
    expect(result.example_ids.filter((id) => selected.has(id))).toEqual([
      firstIds[2],
      lastId,
    ]);
    expect(get).toHaveBeenCalledTimes(2);
    const requestParams = {
      runs: "run-b,run-a",
      baseline: "run-a",
      filters: filters.length ? JSON.stringify(filters) : undefined,
      sort,
      search: search || undefined,
      ids_only: true,
      page_size: 1000,
    };
    expect(get).toHaveBeenNthCalledWith(1, url, expect.objectContaining({
      params: { ...requestParams, page: 0 },
    }));
    expect(get).toHaveBeenNthCalledWith(2, url, expect.objectContaining({
      params: { ...requestParams, page: 1 },
    }));
  });

  it.each([
    { label: "empty", ids: [] },
    { label: "single-page", ids: exampleIds.slice(0, 2) },
  ])("preserves the public response shape for a complete $label selection", async ({ ids }) => {
    get.mockResolvedValueOnce(pageResponse({
      ids,
      total: ids.length,
      hasMore: false,
      next: null,
    }));

    await expect(fetchResultExampleIds("dataset-1", view)).resolves.toEqual({
      total: ids.length,
      example_ids: ids,
    });
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("rejects a continuation request failure instead of returning the first page", async () => {
    const failure = new Error("Continuation unavailable");
    get.mockResolvedValueOnce(pageResponse()).mockRejectedValueOnce(failure);

    await expect(fetchResultExampleIds("dataset-1", view)).rejects.toBe(failure);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it.each([
    { label: "selection identity", overrides: { selectionId: "selection-2" } },
    { label: "population total", overrides: { total: 1002 } },
  ])("rejects $label drift without a partial list", async ({ overrides }) => {
    get.mockResolvedValueOnce(pageResponse()).mockResolvedValueOnce(finalPage(overrides));

    await expect(fetchResultExampleIds("dataset-1", view)).rejects.toThrow(
      /Results changed while loading/,
    );
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("rejects a terminal page that leaves the advertised population incomplete", async () => {
    get.mockResolvedValueOnce(pageResponse()).mockResolvedValueOnce(finalPage({ ids: [] }));

    await expect(fetchResultExampleIds("dataset-1", view)).rejects.toThrow(
      /Incomplete Results population/,
    );
    expect(get).toHaveBeenCalledTimes(2);
  });

  it.each([
    { label: "non-advancing", next: 0, error: /continuation did not advance/ },
    { label: "skipped", next: 2, error: /continuation skipped a page/ },
  ])("rejects a $label continuation", async ({ next, error }) => {
    get.mockResolvedValueOnce(pageResponse({ next }));

    await expect(fetchResultExampleIds("dataset-1", view)).rejects.toThrow(error);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("rejects a continuation whose authorized scope is incomplete", async () => {
    get.mockResolvedValueOnce(pageResponse()).mockResolvedValueOnce(finalPage({ complete: false }));

    await expect(fetchResultExampleIds("dataset-1", view)).rejects.toThrow(
      /Results population is incomplete/,
    );
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("rejects duplicate identities across pages rather than silently losing rows", async () => {
    get.mockResolvedValueOnce(pageResponse()).mockResolvedValueOnce(finalPage({ ids: [firstIds[0]] }));

    await expect(fetchResultExampleIds("dataset-1", view)).rejects.toThrow(
      /Duplicate or invalid identity/,
    );
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("retains the shared 10000-record bound instead of returning a truncated selection", async () => {
    get.mockResolvedValueOnce(pageResponse({ total: 10001 }));

    await expect(fetchResultExampleIds("dataset-1", view)).rejects.toThrow(
      /exceeds 10000 records/,
    );
    expect(get).toHaveBeenCalledTimes(1);
  });
});
