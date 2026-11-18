import { describe, expect, it } from "vitest";

import { SimulateApiHarnessEnvironmentsCompareRunsResponse } from "src/generated/api-contracts/api.zod";
import { compareRunsResponse } from "../_fixtures/compareRuns";

// A call's fields the generated schema reads as never null, which the calls
// page's own serializer sends as null.
const NULLABLE_CALL_FIELDS = new Set([
  "csat_reason",
  "failure_domain",
  "failure_code",
  "trial_index",
]);
// The generated schema reads "any JSON" as an object, so an evaluation's plain
// value, and its null `passed`, are the server's own shape there.
const generatorGap = (issue) => {
  if (issue.code !== "invalid_type" || issue.path[0] !== "rows") return false;
  const field = issue.path.at(-1);
  if (issue.path.includes("evaluations")) {
    return (
      field === "value" || (field === "passed" && issue.received === "null")
    );
  }
  return (
    issue.path[2] === "calls" &&
    issue.path.length === 6 &&
    issue.received === "null" &&
    NULLABLE_CALL_FIELDS.has(field)
  );
};

const issuesOf = (response) =>
  (
    SimulateApiHarnessEnvironmentsCompareRunsResponse.safeParse(response).error
      ?.issues ?? []
  ).filter((issue) => !generatorGap(issue));

describe("compare runs fixture", () => {
  it("matches the generated response contract", () => {
    expect(issuesOf(compareRunsResponse())).toEqual([]);
  });

  it("allows a row with none of its scenario-level columns", () => {
    const response = compareRunsResponse();
    Object.assign(response.rows[0], {
      persona: null,
      persona_details: null,
      situation: null,
      ideal_outcome: null,
    });

    expect(issuesOf(response)).toEqual([]);
  });

  it("allows a call the harness never tagged with a trial", () => {
    const response = compareRunsResponse();
    response.rows[0].calls[response.baseline_execution_id][0].trial_index =
      null;

    expect(issuesOf(response)).toEqual([]);
  });

  it("would catch a field the mappers read going missing or null", () => {
    const response = compareRunsResponse();
    response.rows[0].row_id = null;
    delete response.runs[0].summary.pass_rate;
    delete response.runs[0].execution.summary.pass_rate;
    response.runs[0].execution.summary.measured = null;

    const caught = issuesOf(response).map((issue) => issue.path.join("."));

    expect(caught).toEqual(
      expect.arrayContaining([
        "rows.0.row_id",
        "runs.0.summary.pass_rate",
        "runs.0.execution.summary.pass_rate",
        "runs.0.execution.summary.measured",
      ]),
    );
  });
});
