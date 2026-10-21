import { describe, expect, it } from "vitest";

import {
  AMBIGUOUS_SPAN_SELECTION_MESSAGE,
  getSpanPhysicalRowId,
  parseSpanPhysicalRowId,
  spanSourceIdsFromPhysicalRowIds,
} from "../spanPhysicalIdentity";

describe("span physical identity", () => {
  const first = {
    project_id: "project-a",
    trace_id: "trace-a",
    span_id: "reused-span",
    start_time: "2026-08-09T00:00:00Z",
  };
  const second = {
    project_id: "project-a",
    trace_id: "trace-b",
    span_id: "reused-span",
    start_time: "2026-08-09T00:01:00Z",
  };

  it("keeps duplicate OpenTelemetry span IDs in different traces as distinct rows", () => {
    const firstRowId = getSpanPhysicalRowId(first);
    const secondRowId = getSpanPhysicalRowId(second);

    expect(firstRowId).not.toBe(secondRowId);
    expect(parseSpanPhysicalRowId(firstRowId)).toEqual({
      projectId: first.project_id,
      traceId: first.trace_id,
      spanId: first.span_id,
      startTime: first.start_time,
    });
    expect(parseSpanPhysicalRowId(secondRowId)).toEqual({
      projectId: second.project_id,
      traceId: second.trace_id,
      spanId: second.span_id,
      startTime: second.start_time,
    });
  });

  it("keeps otherwise identical spans in different projects as distinct rows", () => {
    const otherProject = { ...first, project_id: "project-b" };

    expect(getSpanPhysicalRowId(first)).not.toBe(
      getSpanPhysicalRowId(otherProject),
    );
  });

  it("decodes unambiguous selections only at the existing source_id boundary", () => {
    expect(
      spanSourceIdsFromPhysicalRowIds([
        getSpanPhysicalRowId(first),
        getSpanPhysicalRowId({ ...second, span_id: "span-b" }),
      ]),
    ).toEqual(["reused-span", "span-b"]);
  });

  it("fails closed when two physical rows alias to one bare source_id", () => {
    expect(() =>
      spanSourceIdsFromPhysicalRowIds([
        getSpanPhysicalRowId(first),
        getSpanPhysicalRowId(second),
      ]),
    ).toThrow(AMBIGUOUS_SPAN_SELECTION_MESSAGE);
  });

  it("rejects rows missing any part of the physical tuple", () => {
    expect(
      getSpanPhysicalRowId({
        project_id: "project-a",
        trace_id: "trace-a",
        span_id: "span-a",
      }),
    ).toBeNull();
    expect(
      getSpanPhysicalRowId({
        trace_id: "trace-a",
        span_id: "span-a",
        start_time: "2026-08-09T00:00:00Z",
      }),
    ).toBeNull();
    expect(() => spanSourceIdsFromPhysicalRowIds(["span-a"])).toThrow(
      "missing their canonical identity",
    );
  });
});
