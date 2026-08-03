import { describe, expect, it } from "vitest";

import {
  buildVersionMappingPayload,
  reconcileMappingToVariables,
  resolveVersionMapping,
  resolveVersionTracingProjectId,
  unwrapModeMapping,
} from "../evalMappingPersistence";

describe("buildVersionMappingPayload", () => {
  it("emits snake_case keys the backend persists, bucketed per mode", () => {
    const payload = buildVersionMappingPayload(
      { tracing: { response: "attributes.output.value" } },
      "proj-123",
    );
    expect(payload).toEqual({
      mapping: { tracing: { response: "attributes.output.value" } },
      tracing_project_id: "proj-123",
    });
  });

  // The two tabs map the same variables to different value spaces, so one flat
  // blob cannot say which produced it. Saving from Tracing must not drop what
  // the Dataset tab had — the payload replaces the whole field.
  it("carries both modes so saving from one tab doesn't wipe the other", () => {
    expect(
      buildVersionMappingPayload(
        {
          tracing: { response: "attributes.output.value" },
          dataset: { response: "col_response" },
        },
        "p1",
      ).mapping,
    ).toEqual({
      tracing: { response: "attributes.output.value" },
      dataset: { response: "col_response" },
    });
  });

  it("omits a mode that has nothing mapped", () => {
    expect(
      buildVersionMappingPayload({ tracing: { a: "b" }, dataset: {} }, null)
        .mapping,
    ).toEqual({ tracing: { a: "b" } });
  });

  it("copies each bucket so later edits don't mutate the sent payload", () => {
    const live = { tracing: { response: "a" } };
    const payload = buildVersionMappingPayload(live, "p1");
    live.tracing.response = "mutated";
    expect(payload.mapping).toEqual({ tracing: { response: "a" } });
  });

  // The wipe regression. The backend gates on `is not None`, so `{}` is a real
  // value that overwrites, while null means leave-alone. A save with nothing
  // mapped anywhere must never send `{}` — that silently clears a mapping the
  // user saved earlier.
  it("sends null, NOT {}, when nothing is mapped on either tab", () => {
    expect(
      buildVersionMappingPayload({ tracing: {}, dataset: {} }, "p1"),
    ).toEqual({ mapping: null, tracing_project_id: "p1" });
  });

  it("sends null mapping when there is no mapping state at all", () => {
    expect(buildVersionMappingPayload(null, "p1")).toEqual({
      mapping: null,
      tracing_project_id: "p1",
    });
  });

  it("sends null tracing_project_id when no project is selected", () => {
    expect(
      buildVersionMappingPayload({ tracing: { a: "b" } }, null)
        .tracing_project_id,
    ).toBeNull();
  });

  it("sends both keys as null when nothing is set", () => {
    expect(buildVersionMappingPayload(null, null)).toEqual({
      mapping: null,
      tracing_project_id: null,
    });
  });

  it("treats a non-object bucket as no mapping", () => {
    expect(
      buildVersionMappingPayload({ tracing: "nope" }, null).mapping,
    ).toBeNull();
    expect(buildVersionMappingPayload("nope", null).mapping).toBeNull();
  });
});

describe("resolveVersionMapping", () => {
  it("reads only the requested mode's bucket", () => {
    const version = {
      id: "v1",
      mapping: {
        tracing: { response: "trace.out" },
        dataset: { response: "col_out" },
      },
    };
    expect(resolveVersionMapping(version, "tracing")).toEqual({
      response: "trace.out",
    });
    expect(resolveVersionMapping(version, "dataset")).toEqual({
      response: "col_out",
    });
  });

  // Column ids are not trace-field paths, so a tab must never inherit the
  // other's values.
  it("returns nothing for a mode the version never mapped", () => {
    const version = { id: "v1", mapping: { dataset: { response: "col_out" } } };
    expect(resolveVersionMapping(version, "tracing")).toEqual({});
  });

  it("defaults to {} for a pre-snapshot version with NULL mapping", () => {
    expect(
      resolveVersionMapping({ id: "v0", mapping: null }, "tracing"),
    ).toEqual({});
    expect(resolveVersionMapping({ id: "v0" }, "dataset")).toEqual({});
    expect(resolveVersionMapping(null, "tracing")).toEqual({});
  });

  // Rows written before the envelope existed are flat and carry no
  // discriminator. A saved tracing project is the only signal they have.
  it("routes a legacy flat mapping by whether a tracing project was saved", () => {
    const traced = {
      mapping: { response: "trace.out" },
      tracing_project_id: "proj-1",
    };
    expect(resolveVersionMapping(traced, "tracing")).toEqual({
      response: "trace.out",
    });
    expect(resolveVersionMapping(traced, "dataset")).toEqual({});

    const untraced = { mapping: { response: "col_out" } };
    expect(resolveVersionMapping(untraced, "dataset")).toEqual({
      response: "col_out",
    });
    expect(resolveVersionMapping(untraced, "tracing")).toEqual({});
  });

  // An eval whose variable is literally named `tracing` must not be mistaken
  // for an envelope: buckets hold objects, flat mappings hold field paths.
  it("does not mistake a variable named like a mode for an envelope", () => {
    const version = {
      mapping: { tracing: "attributes.output.value" },
      tracing_project_id: "proj-1",
    };
    expect(resolveVersionMapping(version, "tracing")).toEqual({
      tracing: "attributes.output.value",
    });
  });
});

describe("unwrapModeMapping", () => {
  it("pulls a bucket out of an envelope", () => {
    expect(
      unwrapModeMapping(
        { tracing: { a: "b" }, dataset: { a: "c" } },
        "dataset",
      ),
    ).toEqual({ a: "c" });
  });

  it("passes a flat mapping through unchanged, so it is safe to apply twice", () => {
    expect(unwrapModeMapping({ a: "b" }, "tracing")).toEqual({ a: "b" });
    expect(
      unwrapModeMapping(unwrapModeMapping({ a: "b" }, "tracing"), "tracing"),
    ).toEqual({ a: "b" });
  });

  it("returns {} for nothing", () => {
    expect(unwrapModeMapping(null, "tracing")).toEqual({});
    expect(unwrapModeMapping({}, "tracing")).toEqual({});
  });
});

describe("reconcileMappingToVariables", () => {
  // Renaming a variable used to leave the old key mapped forever, re-saved on
  // every Save Version.
  it("drops entries whose variable no longer exists", () => {
    expect(
      reconcileMappingToVariables({ question: "a.b", removed: "c.d" }, [
        "question",
        "answer",
      ]),
    ).toEqual({ question: "a.b" });
  });

  // Fail-safe: an unknown variable set must never be treated as an empty one.
  it("leaves the mapping alone when the variable set is not known yet", () => {
    expect(reconcileMappingToVariables({ question: "a.b" }, [])).toEqual({
      question: "a.b",
    });
    expect(reconcileMappingToVariables({ question: "a.b" }, null)).toEqual({
      question: "a.b",
    });
  });

  it("returns {} when there is no mapping", () => {
    expect(reconcileMappingToVariables(null, ["a"])).toEqual({});
    expect(reconcileMappingToVariables({}, ["a"])).toEqual({});
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
