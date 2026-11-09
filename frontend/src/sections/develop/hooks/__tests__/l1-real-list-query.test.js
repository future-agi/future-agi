// L1 (the capability contract): the Datasets list on the real backend reads dev's
// `GET develops/get-datasets/` with only the fields its strict
// DatasetListQuerySerializer takes. The allowed set is read from the
// backend serializer itself (futureagi/model_hub/serializers/
// develop_dataset_contracts.py), never typed here by hand.
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it, vi } from "vitest";
import { madeRealSurfaceIds } from "src/launch/madeReal";

const getMock = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { get: (...a) => getMock(...a) },
  endpoints: {
    develop: {
      getDatasets: () => "/model-hub/develops/get-datasets/",
      w9: { datasetsQuery: "/model-hub/datasets/query/" },
    },
  },
}));

import {
  datasetListQueryConfig,
  realDatasetListParams,
  realDatasetListResult,
  REAL_MAX_PAGE_SIZE,
} from "../useDatasetListQuery";
import { normalizeDatasetRow } from "../useDatasetsList";
import {
  REAL_SORT_FIELDS,
  datasetCaps,
  isRealBackend,
  setRealBackendForTests,
} from "src/sections/develop/realBackendCaps";

const CONTRACTS = path.resolve(
  process.cwd(),
  "../futureagi/model_hub/serializers/develop_dataset_contracts.py",
);
const VIEWS = path.resolve(
  process.cwd(),
  "../futureagi/model_hub/views/develop_dataset.py",
);

/** The field names of a DRF serializer class, read from the source. */
function serializerFields(file, className) {
  const src = fs.readFileSync(file, "utf8");
  const start = src.indexOf(`class ${className}(`);
  expect(start, `${className} in ${file}`).toBeGreaterThan(-1);
  const rest = src.slice(start + 1);
  const end = rest.search(/\nclass /);
  const body = end < 0 ? rest : rest.slice(0, end);
  return [...body.matchAll(/^ {4}(\w+) = serializers\./gm)].map((m) => m[1]);
}

/** GetDatasetsView's column_mapping keys (the sort fields dev orders by). */
function devSortFields() {
  const src = fs.readFileSync(VIEWS, "utf8");
  const view = src.slice(src.indexOf("class GetDatasetsView("));
  const mapping = view.slice(
    view.indexOf("column_mapping = {"),
    view.indexOf("}", view.indexOf("column_mapping = {")),
  );
  return [...mapping.matchAll(/"(\w+)":/g)].map((m) => m[1]);
}

afterEach(() => {
  setRealBackendForTests(null);
  getMock.mockReset();
});

describe("L1 the real-backend switch", () => {
  it("is off in unit tests unless a test turns it on", () => {
    expect(isRealBackend()).toBe(false);
    setRealBackendForTests(true);
    expect(isRealBackend()).toBe(true);
    expect(datasetCaps()).toMatchObject({
      real: true,
      listQuery: "get-datasets",
      listFilters: false,
      listGroupBy: false,
      listFolders: false,
      listStars: false,
      listSavedViews: false,
      listOrganize: ["metadata", "agents"].some((part) =>
        madeRealSurfaceIds().has(`list.organize.${part}`),
      ),
      listRunningPoll: false,
      searchScope: "name",
      openOnExperimentsTab: false,
      gridWrites: "classic",
      pendingChanges: false,
      saveVersion: false,
    });
  });

  it("keeps the full DSX set in prototype mode", () => {
    const caps = datasetCaps(false);
    expect(caps.listQuery).toBe("w9-query");
    expect(caps.gridWrites).toBe("versioned");
    expect(caps.pendingChanges && caps.saveVersion && caps.listFilters).toBe(
      true,
    );
  });
});

describe("L1 realDatasetListParams", () => {
  const allowed = serializerFields(CONTRACTS, "DatasetListQuerySerializer");

  it("reads dev's strict list serializer (sanity)", () => {
    // `starred` is the personal Starred filter; it is sent only when set.
    expect(allowed.sort()).toEqual(
      [
        "page",
        "page_size",
        "search_text",
        "sort",
        "starred",
        "folder_path",
      ].sort(),
    );
  });

  it("sends only fields dev's strict serializer takes, whatever the view state holds", () => {
    const params = realDatasetListParams({
      page: 2,
      pageSize: 50,
      search: "  refund ",
      sortBy: "name",
      sortOrder: "asc",
      filters: '[{"column_id":"tag"}]',
      groupBy: "owner",
      starredFirst: false,
    });
    for (const key of Object.keys(params)) expect(allowed).toContain(key);
    expect(params).toEqual({
      page: 2,
      page_size: 50,
      search_text: "refund",
      sort: JSON.stringify([{ column_id: "name", type: "ascending" }]),
    });
  });

  it("sorts only by fields GetDatasetsView orders by, newest first otherwise", () => {
    const dev = devSortFields();
    for (const f of REAL_SORT_FIELDS) expect(dev).toContain(f);
    // The DSX default (`updated_at`) and run columns are not dev's.
    for (const sortBy of ["updated_at", "score_now", "last_experiment_at"]) {
      const sort = JSON.parse(realDatasetListParams({ sortBy }).sort);
      expect(sort).toEqual([{ column_id: "created_at", type: "descending" }]);
    }
    expect(
      JSON.parse(
        realDatasetListParams({ sortBy: "created_at", sortOrder: "desc" }).sort,
      ),
    ).toEqual([{ column_id: "created_at", type: "descending" }]);
  });

  it("keeps page_size within dev's bound and drops an empty search", () => {
    expect(realDatasetListParams({ pageSize: 500 }).page_size).toBe(
      REAL_MAX_PAGE_SIZE,
    );
    expect(REAL_MAX_PAGE_SIZE).toBe(100);
    const p = realDatasetListParams({ search: "   ", page: -3 });
    expect(p.page).toBe(0);
    expect("search_text" in p).toBe(false);
  });
});

describe("L1 the list query on the real backend", () => {
  it("asks get-datasets (never datasets/query/) and keeps no running poll", async () => {
    getMock.mockResolvedValue({
      data: {
        result: {
          datasets: [
            {
              id: "d1",
              name: "refunds",
              number_of_datapoints: 3,
              number_of_experiments: 0,
              number_of_optimisations: 0,
              derived_datasets: 0,
              created_at: "2026-10-25 23:40",
              dataset_type: "GenerativeLLM",
            },
          ],
          total_pages: 1,
          total_count: 1,
        },
      },
    });
    const cfg = datasetListQueryConfig(
      { page: 0, pageSize: 25, search: "ref", filters: "x", groupBy: "g" },
      true,
    );
    expect(cfg.refetchInterval).toBe(false);
    expect(cfg.queryKey.slice(0, 2)).toEqual(["datasets", "list"]);
    const out = await cfg.queryFn();
    expect(getMock).toHaveBeenCalledTimes(1);
    const [url, { params }] = getMock.mock.calls[0];
    expect(url).toBe("/model-hub/develops/get-datasets/");
    expect(params).toEqual({
      page: 0,
      page_size: 25,
      search_text: "ref",
      sort: JSON.stringify([{ column_id: "created_at", type: "descending" }]),
    });
    expect(out.items[0]).toMatchObject({
      id: "d1",
      name: "refunds",
      numberOfDatapoints: 3,
      createdAt: "2026-10-25 23:40",
    });
    expect(out).toMatchObject({
      total: 1,
      totalPages: 1,
      groups: null,
      folders: [],
      facets: {},
      leftOut: {},
    });
  });

  it("still asks datasets/query/ in prototype mode", async () => {
    getMock.mockResolvedValue({ data: { result: { datasets: [] } } });
    const cfg = datasetListQueryConfig({ page: 0 }, false);
    await cfg.queryFn();
    expect(getMock.mock.calls[0][0]).toBe("/model-hub/datasets/query/");
  });

  it("normalises rows like the list", () => {
    const row = { id: "x", name: "n", number_of_datapoints: 4 };
    expect(realDatasetListResult({ datasets: [row] }).items).toEqual([
      normalizeDatasetRow(row),
    ]);
    expect(realDatasetListResult(undefined)).toMatchObject({
      items: [],
      total: 0,
    });
  });
});
