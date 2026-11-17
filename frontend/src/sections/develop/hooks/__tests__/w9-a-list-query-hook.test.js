// W9 A2-A4, A6 (W9-SCOPE §5): the list query's params from the view state
// and its normalised answer (rows normalised as useDatasetsList does).
import { describe, expect, it } from "vitest";
import {
  anyRunning,
  datasetQueryParams,
  datasetQueryResult,
  queryErrorSentence,
} from "../useDatasetListQuery";
import { normalizeDatasetRow } from "../useDatasetsList";

describe("datasetQueryParams", () => {
  it("sends only what is set, starred first by default", () => {
    expect(datasetQueryParams({ page: 2, pageSize: 50 })).toEqual({
      page: 2,
      page_size: 50,
    });
  });

  it("carries search, sort, filters, group and starred_first=false", () => {
    const f = JSON.stringify([
      {
        column_id: "tag",
        filter_config: {
          filter_type: "categorical",
          filter_op: "in",
          filter_value: ["golden"],
        },
      },
    ]);
    expect(
      datasetQueryParams({
        page: 0,
        pageSize: 25,
        search: "refund",
        sortBy: "name",
        sortOrder: "asc",
        filters: f,
        groupBy: "owner",
        starredFirst: false,
      }),
    ).toEqual({
      page: 0,
      page_size: 25,
      search_text: "refund",
      sort: JSON.stringify([{ column_id: "name", type: "ascending" }]),
      filters: f,
      group_by: "owner",
      starred_first: "false",
    });
    expect(
      JSON.parse(datasetQueryParams({ sortBy: "updated_at" }).sort)[0].type,
    ).toBe("descending");
  });
});

describe("datasetQueryResult", () => {
  const row = {
    id: "d1",
    name: "A",
    number_of_datapoints: 30,
    number_of_experiments: 2,
    folder: "support",
    starred: true,
  };
  it("normalises rows and groups like the list", () => {
    const out = datasetQueryResult({
      datasets: [row],
      total_count: 7,
      total_pages: 1,
      groups: [{ key: "Asha", label: "Asha", count: 1, datasets: [row] }],
      folders: [{ path: "support", count: 1, total: 1 }],
      facets: { owner: [{ value: "Asha", label: "Asha", count: 1 }] },
      left_out: {},
    });
    expect(out.items).toEqual([normalizeDatasetRow(row)]);
    expect(out.items[0]).toMatchObject({
      numberOfDatapoints: 30,
      numberOfExperiments: 2,
      folder: "support",
      starred: true,
    });
    expect(out.total).toBe(7);
    expect(out.groups[0].datasets[0].numberOfDatapoints).toBe(30);
    expect(out.folders).toEqual([{ path: "support", count: 1, total: 1 }]);
    expect(out.facets.owner[0].count).toBe(1);
  });

  it("no groups when not grouped; empty answer is safe", () => {
    expect(datasetQueryResult({ datasets: [] }).groups).toBeNull();
    expect(datasetQueryResult(undefined)).toEqual({
      items: [],
      total: 0,
      totalPages: 0,
      groups: null,
      folders: [],
      facets: {},
      leftOut: {},
    });
  });
});

describe("helpers", () => {
  it("anyRunning looks at rows and grouped rows", () => {
    const running = { last_experiment: { status: "Running" } };
    expect(anyRunning({ items: [running], groups: null })).toBe(true);
    expect(anyRunning({ items: [], groups: [{ datasets: [running] }] })).toBe(
      true,
    );
    expect(anyRunning({ items: [{}], groups: null })).toBe(false);
  });

  it("queryErrorSentence reads a 400's sentence only", () => {
    expect(
      queryErrorSentence({
        response: { status: 400, data: { result: "Unknown field “x”." } },
      }),
    ).toBe("Unknown field “x”.");
    expect(queryErrorSentence({ response: { status: 500 } })).toBeNull();
  });
});
