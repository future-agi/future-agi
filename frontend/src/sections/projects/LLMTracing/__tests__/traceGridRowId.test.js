import { describe, expect, it } from "vitest";
import { getTraceGridRowId, traceIdsFromGridRowIds } from "../traceGridRowId";

const TRACE = "c3c582b0-627a-427d-832d-1f49655803fd";

describe("trace grid row ids", () => {
  it("keeps the bare trace id on a project-pinned grid", () => {
    expect(
      getTraceGridRowId({ trace_id: TRACE, project_id: "project-a" }),
    ).toBe(TRACE);
  });

  it("gives each project's copy of a trace id its own cross-project row id", () => {
    const a = getTraceGridRowId(
      { trace_id: TRACE, project_id: "project-a" },
      { crossProject: true },
    );
    const b = getTraceGridRowId(
      { trace_id: TRACE, project_id: "project-b" },
      { crossProject: true },
    );

    expect(a).toBe(JSON.stringify(["project-a", TRACE]));
    expect(b).not.toBe(a);
  });

  it("falls back to the trace id when a cross-project row has no project", () => {
    expect(getTraceGridRowId({ trace_id: TRACE }, { crossProject: true })).toBe(
      TRACE,
    );
    expect(getTraceGridRowId(null, { crossProject: true })).toBeUndefined();
  });

  it("decodes either row id shape to bare trace ids, once per trace", () => {
    const rowId = (projectId, traceId) =>
      getTraceGridRowId(
        { trace_id: traceId, project_id: projectId },
        { crossProject: true },
      );

    expect(
      traceIdsFromGridRowIds([
        rowId("project-b", TRACE),
        rowId("project-a", TRACE),
        rowId("project-a", "trace-2"),
      ]),
    ).toEqual([TRACE, "trace-2"]);
    expect(traceIdsFromGridRowIds(["trace-1", "", null, "trace-2"])).toEqual([
      "trace-1",
      "trace-2",
    ]);
    expect(traceIdsFromGridRowIds(undefined)).toEqual([]);
  });
});
