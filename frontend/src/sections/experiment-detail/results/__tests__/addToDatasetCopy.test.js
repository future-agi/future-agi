// results-add-to-dataset: the pure copy model — which result columns a copy
// carries, the default column map, the request body, and how every answer
// is read (nothing added is never shown as success, and an answer that never
// came back is never shown as "nothing was added").
import { describe, expect, it } from "vitest";
import {
  copyBody,
  copyErrorOf,
  copyOutcomeOf,
  copySources,
  datasetLinkOf,
  defaultCopyMap,
  failureGroups,
  isRetryableFailure,
  newColumnNames,
  retryBody,
  savedInputColumns,
} from "../addToDatasetModel";

const run = { run_id: "r4a", label: "#4A", task_kind: "prompt" };
const sourceCols = [
  { id: "s-in", name: "input" },
  { id: "s-exp", name: "expected" },
];
const metrics = [
  { key: "m-g", name: "Groundedness", runs: ["r4a"] },
  { key: "m-x", name: "Exact match", runs: ["r3a"] },
];

describe("copySources", () => {
  it("lists this dataset's columns, the output and the run's own scores", () => {
    expect(
      copySources({ run, sourceCols, metrics, expectedChoice: "keep" }).map(
        (l) => l.key,
      ),
    ).toEqual(["s-in", "s-exp", "output", "score:m-g"]);
  });

  it("an external run copies the whole input and the row's expected; the output as expected adds its own line", () => {
    const external = { ...run, task_kind: "external" };
    expect(
      copySources({ run: external, sourceCols, expectedChoice: "keep" }).map(
        (l) => l.key,
      ),
    ).toEqual(["inputs", "expected", "output"]);
    expect(
      copySources({ run: external, sourceCols, expectedChoice: "none" }).map(
        (l) => l.key,
      ),
    ).toEqual(["inputs", "output"]);
    expect(
      copySources({ run, sourceCols, expectedChoice: "output_review" }).map(
        (l) => l.key,
      ),
    ).toEqual(["s-in", "s-exp", "expected", "output"]);
  });
});

describe("savedInputColumns", () => {
  it("keeps only the columns some picked row saved with the run", () => {
    const rows = [
      {
        example_id: "e1",
        inputs: { values: { "s-in": "Where is my order?" } },
      },
      {
        example_id: "e2",
        inputs: { values: { "s-in": "Refund?", gone: "x" } },
      },
    ];
    expect(savedInputColumns(sourceCols, rows)).toEqual({
      saved: [{ id: "s-in", name: "input" }],
      unsaved: [{ id: "s-exp", name: "expected" }],
    });
  });

  it("rows without saved inputs on the page keep every column", () => {
    expect(savedInputColumns(sourceCols, [])).toEqual({
      saved: sourceCols,
      unsaved: [],
    });
    expect(
      savedInputColumns(sourceCols, [
        { example_id: "e1", inputs: "plain text" },
        { example_id: "e2", inputs: { values: ["not", "keyed"] } },
      ]),
    ).toEqual({ saved: sourceCols, unsaved: [] });
  });
});

describe("column maps", () => {
  it("another dataset: names map, the expected line claims first, picks are kept", () => {
    const lines = copySources({ run, sourceCols, expectedChoice: "output" });
    const target = [
      { id: "t-exp", name: "expected" },
      { id: "t-in", name: "input" },
    ];
    expect(defaultCopyMap(lines, target)).toEqual({
      expected: "t-exp",
      "s-in": "t-in",
      "s-exp": null,
      output: null,
    });
    // A pick of a column another line has is kept (and said), not undone.
    expect(
      defaultCopyMap(lines, target, { "s-exp": "t-in", "s-in": "t-in" }),
    ).toMatchObject({ "s-exp": "t-in", "s-in": "t-in" });
    // A pick of a column the target does not have falls back to the default.
    expect(defaultCopyMap(lines, target, { "s-in": "gone" })["s-in"]).toBe(
      "t-in",
    );
  });

  it("a new dataset: each copied column gets a name of its own", () => {
    const lines = [
      { key: "s-out", name: "output" },
      { key: "output", name: "output" },
      { key: "score:m-g", name: "Groundedness" },
    ];
    expect(
      newColumnNames(lines, {
        "s-out": true,
        output: true,
        "score:m-g": false,
      }),
    ).toEqual({ "s-out": "output", output: "output (2)" });
  });
});

describe("request bodies", () => {
  it("sends only mapped columns, the picked scores and the needs-review mark", () => {
    const body = copyBody({
      datasetId: "GS",
      runId: "r4a",
      exampleIds: ["e1"],
      target: "new",
      trial: 2,
      expectedChoice: "output_review",
      columnMap: { "s-in": "input", output: null, "score:m-g": "Groundedness" },
      newDatasetName: "Regressions",
      note: "From review",
    });
    expect(body).toEqual({
      source_dataset_id: "GS",
      run_id: "r4a",
      example_ids: ["e1"],
      trial: 2,
      mode: "copy",
      column_map: { "s-in": "input", "score:m-g": "Groundedness" },
      score_columns: ["m-g"],
      expected_from: "output",
      needs_review: true,
      expected_source: "production_output",
      version_description: "From review",
      new_dataset_name: "Regressions",
    });
    const retry = retryBody(body, {
      failures: [{ example_id: "e1" }],
      columnMap: { "s-in": "c1", "score:m-g": "c2" },
    });
    expect(retry).not.toHaveProperty("new_dataset_name");
    expect(retry).toMatchObject({
      example_ids: ["e1"],
      column_map: { "s-in": "c1", "score:m-g": "c2" },
      score_columns: ["m-g"],
      trial: 2,
    });
  });

  it("a retry sends only the rows a second send can add", () => {
    expect(isRetryableFailure({ code: "result_unavailable" })).toBe(true);
    expect(isRetryableFailure({ code: "something_new" })).toBe(true);
    for (const code of [
      "example_unavailable",
      "output_unavailable",
      "score_unavailable",
      "input_unavailable",
      "invalid_result",
      "invalid_value",
    ])
      expect(isRetryableFailure({ code })).toBe(false);
    const retry = retryBody(
      { example_ids: ["e1", "e2", "e3"], new_dataset_name: "Misses" },
      {
        failures: [
          { example_id: "e1", code: "score_unavailable" },
          { example_id: "e2", code: "result_unavailable" },
          { example_id: "e3", code: "input_unavailable" },
        ],
        columnMap: { output: "c-out" },
      },
    );
    expect(retry.example_ids).toEqual(["e2"]);
  });
});

describe("reading the answer", () => {
  const result = (extra) => ({
    dataset_id: "d1",
    dataset_name: "Golden",
    dataset_url: "/dashboard/develop/d1?tab=data",
    rows_requested: 2,
    rows_added: 2,
    rows_failed: 0,
    failures: [],
    column_map: {},
    ...extra,
  });
  const allFailed = {
    status: false,
    type: "client_error",
    code: "no_rows_added",
    detail: "No rows were added. Review the failed rows before trying again.",
    message: "No rows were added. Review the failed rows before trying again.",
    error: "No rows were added. Review the failed rows before trying again.",
    result: result({
      dataset_id: null,
      dataset_url: null,
      rows_added: 0,
      rows_failed: 2,
      failures: [
        { example_id: "e1", code: "result_unavailable" },
        { example_id: "e2", code: "result_unavailable" },
      ],
    }),
  };
  // How the app's client rejects (src/utils/axios): the answer's body spread
  // out with the HTTP status as `statusCode`, and no `response`.
  const appError = (statusCode, body) => ({
    ...body,
    statusCode,
    transportCode: statusCode >= 500 ? "ERR_BAD_RESPONSE" : "ERR_BAD_REQUEST",
    responseHeaders: { "x-request-id": "req-1" },
  });

  it("200, 207 and an answer without counts", () => {
    expect(copyOutcomeOf({ data: { result: result() } }).kind).toBe(
      "completed",
    );
    const partial = copyOutcomeOf({
      data: {
        result: result({
          rows_added: 1,
          rows_failed: 1,
          failures: [{ example_id: "e2", code: "score_unavailable" }],
        }),
      },
    });
    expect(partial.kind).toBe("partial");
    expect(partial.result).toMatchObject({ added: 1, failed: 1, requested: 2 });
    expect(copyOutcomeOf({ data: { status: true } }).kind).toBe("unknown");
    expect(copyOutcomeOf({ data: { result: "ok" } }).kind).toBe("unknown");
  });

  it("links only to a dataset page", () => {
    expect(datasetLinkOf(result())).toBe("/dashboard/develop/d1?tab=data");
    expect(
      datasetLinkOf(result({ dataset_url: "https://elsewhere.example/x" })),
    ).toBe("/dashboard/develop/d1?tab=data");
    expect(datasetLinkOf(result({ dataset_id: null, dataset_url: null }))).toBe(
      null,
    );
  });

  it.each([
    [
      {
        message: "Network Error",
        statusCode: undefined,
        transportCode: "ERR_NETWORK",
        responseHeaders: {},
      },
      "unknown",
    ],
    [
      appError(502, { message: "Request failed with status code 502" }),
      "unknown",
    ],
    [appError(504, { 0: "<", 1: "h" }), "unknown"],
    [appError(500, { status: false, detail: "Server error." }), "unknown"],
    [
      appError(503, { status: false, detail: "No rows were added." }),
      "refused",
    ],
    [
      appError(400, {
        status: false,
        detail: "A dataset with this name already exists. Choose another name.",
      }),
      "refused",
    ],
    [appError(403, { detail: "Workspace write access required." }), "refused"],
    [appError(429, {}), "refused"],
    [appError(422, allFailed), "failed"],
    // A raw Axios error (no app client in between) is read the same way.
    [
      { response: { status: 502, data: "<html>Bad gateway</html>" } },
      "unknown",
    ],
    [
      {
        response: {
          status: 403,
          data: { detail: "Workspace write access required." },
        },
      },
      "refused",
    ],
    [{ response: { status: 422, data: allFailed } }, "failed"],
  ])("%j is %s", (error, kind) => {
    expect(copyErrorOf(error).kind).toBe(kind);
  });

  it("a refusal says the server's reason, never the client's own words", () => {
    expect(
      copyErrorOf(
        appError(400, {
          status: false,
          code: "invalid",
          detail:
            "A dataset with this name already exists. Choose another name.",
          message:
            "A dataset with this name already exists. Choose another name.",
          result:
            "A dataset with this name already exists. Choose another name.",
        }),
      ),
    ).toEqual({
      kind: "refused",
      status: 400,
      message: "A dataset with this name already exists. Choose another name.",
    });
    expect(
      copyErrorOf(
        appError(403, {
          detail: "Workspace write access required.",
          result: {},
        }),
      ).message,
    ).toBe("Workspace write access required.");
    // An answer without a body: the client says "Request failed with status
    // code 403", which is not a reason.
    expect(
      copyErrorOf(
        appError(403, { message: "Request failed with status code 403" }),
      ).message,
    ).toBe("You do not have permission to add rows to this dataset.");
    expect(
      copyErrorOf(
        appError(503, {
          status: false,
          detail: "The rows could not be saved. No rows were added.",
        }),
      ).message,
    ).toBe("The rows could not be saved. No rows were added.");
  });

  it("a 422 keeps every failed row, from either error shape", () => {
    for (const error of [
      appError(422, allFailed),
      { response: { status: 422, data: allFailed } },
    ]) {
      const failed = copyErrorOf(error);
      expect(failed.kind).toBe("failed");
      expect(failed.result.link).toBe(null);
      expect(failed.result.failed).toBe(2);
      expect(
        failureGroups(failed.result.failures, [
          { example_id: "e1", row_label: "18" },
        ]),
      ).toEqual([
        {
          text: "This try has no finished result to copy.",
          count: 2,
          labels: ["Row 18"],
        },
      ]);
    }
  });
});
