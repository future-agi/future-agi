import { describe, expect, it } from "vitest";

import { ModelHubGetEvalLogsListResponse as Response } from "src/generated/api-contracts/api.zod";

// GET /model-hub/get-eval-logs?include_source_navigation=true adds
// result.source_navigation. SourceNavigation.as_dict()
// (futureagi/tracer/services/trace_source_navigation.py) sends kind and every
// id as explicit null for each state that has no link.
const sourceNavigation = Response.shape.result.shape.source_navigation;

const noLink = (status, retryable = false) => ({
  status,
  kind: null,
  project_id: null,
  trace_id: null,
  span_id: null,
  retryable,
});

describe("generated get-eval-logs source_navigation contract", () => {
  it("accepts a ready trace with its exact ids", () => {
    const ready = {
      status: "ready",
      kind: "trace",
      project_id: "11111111-1111-4111-8111-111111111111",
      trace_id: "22222222-2222-4222-8222-222222222222",
      span_id: "f9da550bd36da78b",
      retryable: false,
    };
    expect(sourceNavigation.parse(ready)).toEqual(ready);
  });

  it.each([
    ["unavailable"],
    ["ambiguous_reference"],
    ["no_reference"],
    ["invalid_reference"],
  ])("accepts %s with kind and every id null", (status) => {
    expect(sourceNavigation.safeParse(noLink(status)).success).toBe(true);
  });

  it("accepts a retryable failure with kind and every id null", () => {
    expect(
      sourceNavigation.safeParse(noLink("temporarily_unavailable", true))
        .success,
    ).toBe(true);
  });

  it("still rejects a ready payload whose span id is empty", () => {
    expect(
      sourceNavigation.safeParse({
        ...noLink("ready"),
        kind: "trace",
        span_id: "",
      }).success,
    ).toBe(false);
  });
});
