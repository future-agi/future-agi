import { describe, expect, it } from "vitest";

import {
  buildVersionMappingPayload,
  resolveVersionMapping,
  resolveVersionTracingProjectId,
} from "../evalMappingPersistence";

describe("buildVersionMappingPayload", () => {
  it("emits snake_case keys the backend persists", () => {
    const payload = buildVersionMappingPayload(
      { response: "attributes.output.value" },
      "proj-123",
    );
    expect(payload).toEqual({
      mapping: { response: "attributes.output.value" },
      tracing_project_id: "proj-123",
    });
  });

  it("copies the mapping so later edits don't mutate the sent payload", () => {
    const live = { response: "a" };
    const payload = buildVersionMappingPayload(live, "p1");
    live.response = "mutated";
    expect(payload.mapping).toEqual({ response: "a" });
  });

  it("omits mapping when the tab has no mapping state", () => {
    expect(buildVersionMappingPayload(null, "p1")).toEqual({
      tracing_project_id: "p1",
    });
  });

  it("omits tracing_project_id when no project is selected", () => {
    expect(buildVersionMappingPayload({ a: "b" }, null)).toEqual({
      mapping: { a: "b" },
    });
  });

  it("returns an empty object when nothing is set", () => {
    expect(buildVersionMappingPayload(null, null)).toEqual({});
  });
});

describe("resolveVersionMapping", () => {
  it("reads the real `mapping` key the version list returns", () => {
    const version = { id: "v1", mapping: { response: "trace.out" } };
    expect(resolveVersionMapping(version)).toEqual({ response: "trace.out" });
  });

  it("defaults to {} for a pre-snapshot version with NULL mapping", () => {
    expect(resolveVersionMapping({ id: "v0", mapping: null })).toEqual({});
    expect(resolveVersionMapping({ id: "v0" })).toEqual({});
    expect(resolveVersionMapping(null)).toEqual({});
  });
});

describe("resolveVersionTracingProjectId", () => {
  it("reads the real `tracing_project_id` key", () => {
    expect(
      resolveVersionTracingProjectId({ tracing_project_id: "proj-9" }),
    ).toBe("proj-9");
  });

  it("returns null when absent", () => {
    expect(resolveVersionTracingProjectId({})).toBeNull();
    expect(resolveVersionTracingProjectId(null)).toBeNull();
  });
});
