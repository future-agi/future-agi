import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildErrorFeedAddEvalsPath,
  resolveErrorFeedAddEvalsContext,
} from "../buildErrorFeedAddEvalsDraft";

const PROJECT_ID = "d050fd30-b99e-413b-b5ee-3fa69b8c0d2c";
const TRACE_ID = "59fba958-6683-4a85-a084-06aceb56016d";
const ROW_TRACE_ID = "3f7744b2-8961-4b3d-97fb-a74360bb5657";

describe("Error Feed Add Evals draft", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("crypto", {
      randomUUID: () => "error-feed-draft-id",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resolves project and representative trace context from camelized detail rows", () => {
    expect(
      resolveErrorFeedAddEvalsContext({
        clusterId: "EF123",
        projectId: PROJECT_ID,
        traceId: ROW_TRACE_ID,
        representativeTrace: { traceId: TRACE_ID },
      }),
    ).toEqual({
      clusterId: "EF123",
      projectId: PROJECT_ID,
      traceId: TRACE_ID,
    });
  });

  it("builds a trace-scoped task draft that returns to the Error Feed issue", () => {
    const path = buildErrorFeedAddEvalsPath({
      error: {
        cluster_id: "EF456",
        project_id: PROJECT_ID,
        trace_id: ROW_TRACE_ID,
      },
      returnTo: "/dashboard/error-feed/EF456",
    });

    const url = new URL(path, "http://localhost");
    expect(url.pathname).toBe("/dashboard/tasks/create");
    expect(url.searchParams.get("project")).toBe(PROJECT_ID);
    expect(url.searchParams.get("draft")).toBe("error-feed-draft-id");
    expect(url.searchParams.get("returnTo")).toBe(
      "/dashboard/error-feed/EF456",
    );

    const draft = JSON.parse(
      localStorage.getItem("task-draft-error-feed-draft-id"),
    );
    expect(draft.values).toMatchObject({
      project: PROJECT_ID,
      rowType: "traces",
      spansLimit: 100000,
      runType: "historical",
    });
    expect(draft.values.filters).toEqual([
      expect.objectContaining({
        property: "trace_id",
        propertyId: "trace_id",
        fieldCategory: "system",
        filterConfig: {
          filterType: "text",
          filterOp: "equals",
          filterValue: ROW_TRACE_ID,
        },
      }),
    ]);
  });
});
