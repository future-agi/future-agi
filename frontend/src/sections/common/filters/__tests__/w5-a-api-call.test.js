// the interface contract · the results ids request (runResultsParams + ids_only) the
// expression count and custom columns send. L7c g1: no Copy as API call is
// printed from it any more (the capability contract a), so the snippet test is gone.
import { describe, expect, it } from "vitest";
import { runResultsParams } from "src/api/develop/run-results";
import {
  FI_BASE_URL,
  fiGetRequest,
} from "src/sections/common/commands/requestSnippets";
import * as apiCallModel from "../apiCallModel";
import {
  resultsIdsParams,
  resultsIdsRequest,
  sameRequestAt,
} from "../apiCallModel";
import { candidateView } from "../resultsDoorModel";

const VIEW = {
  runIds: ["r-base", "r-cand"],
  baselineId: "r-base",
  pills: [{ type: "split", values: ["smoke"] }],
  show: "all",
  search: "refund",
  sort: "delta:eval:correctness:r-cand:asc",
  agg: "avg",
};

describe("fiGetRequest", () => {
  it("prints the query in the given order, without empty values", () => {
    const req = fiGetRequest("/model-hub/x/", {
      b: "2",
      a: "1",
      c: "",
      d: null,
      e: true,
    });
    expect(req).toEqual({
      method: "GET",
      url: `${FI_BASE_URL}/model-hub/x/?b=2&a=1&e=true`,
      headers: { "X-Api-Key": "FI_API_KEY", "X-Secret-Key": "FI_SECRET_KEY" },
    });
  });
});

describe("results API call", () => {
  it("is the grid's params plus the candidate pill and ids_only", () => {
    const view = candidateView(VIEW, VIEW.pills, "#2B.correctness = fail");
    expect(resultsIdsParams(view)).toEqual({
      ...runResultsParams(view),
      ids_only: "true",
    });
    const url = new URL(resultsIdsRequest("ds-1", view).url);
    expect(url.origin).toBe(FI_BASE_URL);
    expect(url.pathname).toBe("/model-hub/datasets/ds-1/results/");
    // The candidate view holds the expression pill; the printed filters are
    // exactly what the grid's own builder sends for that view (the pill
    // reaches the wire once the interface contract registers its type: w5-a-r3-expression
    // .world.test.js, seam.patch).
    expect(view.pills).toEqual([
      { type: "split", values: ["smoke"] },
      expect.objectContaining({
        type: "expression",
        expr: "#2B.correctness = fail",
      }),
    ]);
    expect(url.searchParams.get("filters")).toBe(
      runResultsParams(view).filters,
    );
    expect(JSON.parse(url.searchParams.get("filters"))[0]).toEqual({
      type: "split",
      values: ["smoke"],
    });
    expect(url.searchParams.get("ids_only")).toBe("true");
    expect(url.searchParams.get("sort")).toBe(VIEW.sort);
    expect([...url.searchParams.keys()]).toEqual([
      "runs",
      "baseline",
      "filters",
      "search",
      "sort",
      "agg",
      "ids_only",
    ]);
  });

  it("no longer builds printed snippets", () => {
    expect(apiCallModel.resultsApiSnippets).toBeUndefined();
    expect(apiCallModel.API_CALL_NOTE).toBeUndefined();
  });

  it("re-targets the same request at another origin", () => {
    const req = resultsIdsRequest("ds-1", candidateView(VIEW, [], "a = 1"));
    const at = sameRequestAt(req, "http://127.0.0.1:8999/");
    expect(
      at.startsWith(
        "http://127.0.0.1:8999/model-hub/datasets/ds-1/results/?runs=",
      ),
    ).toBe(true);
    expect(new URL(at).search).toBe(new URL(req.url).search);
  });
});
