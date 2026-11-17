import { beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: http,
  endpoints: {
    develop: {
      getRowData: (id) => `/datasets/${id}/get-row-data/`,
      getDatasetDetail: (id) => `/datasets/${id}/table/`,
    },
  },
}));
vi.mock("src/launch/launchSwitch", () => ({ isSurfaceShown: () => false }));
vi.mock("src/sections/develop-detail/DataTab/Versions/pendingRows", () => ({
  isPendingRowId: () => false,
}));
vi.mock("../dataset-versions", () => ({ parseVersionParam: () => null }));
import { fetchRowInView } from "../row-view";

const search = { key: "Needle", type: ["text", "image", "audio"] };
const current = { row_id: "matching-row", column: { cell_value: "Needle" } };
const missing = () => ({ response: { status: 404 } });

beforeEach(() => vi.resetAllMocks());

describe("searched dataset row detail", () => {
  it("preserves search and uses only the searched neighbors from the row API", async () => {
    http.post.mockResolvedValue({ data: { result: {
      current, next: { row_id: ["next-matching-row"] },
    } } });
    const row = await fetchRowInView("dataset", "matching-row", { search });
    expect(http.post).toHaveBeenCalledExactlyOnceWith(
      "/datasets/dataset/get-row-data/",
      { row_id: "matching-row", filters: [], sort: [], search },
    );
    expect(row.current).toEqual(current);
    expect(row.next).toEqual(["next-matching-row"]);
    expect(row.outside).toBe(false);
  });

  it("reads an outside-search row alone without leaking unfiltered neighbors", async () => {
    http.post.mockRejectedValueOnce(missing()).mockResolvedValueOnce({
      data: { result: { current, next: { row_id: ["not-a-match"] } } },
    });
    http.get.mockResolvedValue({ data: { result: { metadata: { total_rows: 2 } } } });
    const row = await fetchRowInView("dataset", "matching-row", { search });
    expect(http.post).toHaveBeenNthCalledWith(2, "/datasets/dataset/get-row-data/", {
      row_id: "matching-row", filters: [], sort: [],
    });
    expect(http.get.mock.calls[0][1].params.search).toBe(JSON.stringify(search));
    expect(row).toMatchObject({
      current, prev: [], next: [], outside: true, notFound: false,
      position: { index: null, total: 2 },
    });
  });

  it("reports a deleted row as not found after both reads return 404", async () => {
    http.post.mockRejectedValue(missing());
    http.get.mockResolvedValue({ data: { result: { metadata: { total_rows: 0 } } } });
    const row = await fetchRowInView("dataset", "deleted-row", { search });
    expect(row).toMatchObject({ current: null, next: [], notFound: true, outside: false });
  });

  it("does not disguise a contract failure as an outside-search row", async () => {
    const error = { response: { status: 400, data: { search: ["Unknown field."] } } };
    http.post.mockRejectedValue(error);
    await expect(fetchRowInView("dataset", "matching-row", { search })).rejects.toBe(error);
    expect(http.post).toHaveBeenCalledTimes(1);
    expect(http.get).not.toHaveBeenCalled();
  });

  it("sends no search after the grid search is cleared", async () => {
    http.post.mockResolvedValue({ data: { result: { current } } });
    await fetchRowInView("dataset", "matching-row");
    expect(http.post.mock.calls[0][1]).toEqual({
      row_id: "matching-row", filters: [], sort: [],
    });
  });
});
