import { describe, expect, it, vi } from "vitest";
import applySavedEntityTags from "../applySavedEntityTags";

const PROJECT = "project-1";
const target = (span, overrides = {}) => ({
  project_id: PROJECT,
  trace_id: "trace-1",
  span_id: span,
  start_hour: "2026-11-08T10:00:00+00:00",
  observation_type: "span",
  service_name: "svc",
  expected_start_time: "2026-11-08T10:15:20.123456+00:00",
  expected_version: "1",
  ...overrides,
});
const row = (span, { parent = "", sdk = [], revision = 0, ...rest } = {}) => ({
  span_id: span,
  trace_id: "trace-1",
  project_id: PROJECT,
  parent_span_id: parent,
  tags: sdk.map((name) => ({ name })),
  source_tag_names: sdk,
  manual_tags: [],
  tag_revision: revision,
  tag_target: target(span),
  ...rest,
});
const gridApi = (rows) => {
  const nodes = rows.map((data) => ({ data }));
  return {
    nodes,
    forEachNode: vi.fn((visit) => nodes.forEach(visit)),
    refreshCells: vi.fn(),
  };
};
const reviewed = { name: "reviewed", color: "#123456" };
const result = (sdk, revision = 1) => ({
  tags: [...sdk.map((name) => ({ name })), reviewed],
  manual_tags: [reviewed],
  source_tag_names: sdk,
  tag_revision: revision,
});

describe("applySavedEntityTags", () => {
  it("updates the trace row from a save on its root span", () => {
    const traceRow = row("root", { sdk: ["sdk-a"] });
    delete traceRow.parent_span_id;
    const api = gridApi([traceRow]);
    const updated = applySavedEntityTags(api, {
      result: result(["sdk-a"]),
      entity: { tag_target: target("root"), parent_span_id: "" },
      type: "span",
      gridKind: "trace",
    });
    expect(updated).toBe(1);
    expect(traceRow).toMatchObject({
      tags: [{ name: "sdk-a" }, reviewed],
      manual_tags: [reviewed],
      tag_revision: 1,
      tag_target: target("root"),
    });
    expect(api.refreshCells).toHaveBeenCalledWith({
      rowNodes: [api.nodes[0]],
      force: true,
    });
  });

  it("leaves the trace row alone when a child span is saved", () => {
    const traceRow = row("root");
    const api = gridApi([traceRow]);
    const updated = applySavedEntityTags(api, {
      result: result([]),
      entity: { tag_target: target("child"), parent_span_id: "root" },
      type: "span",
      gridKind: "trace",
    });
    expect(updated).toBe(0);
    expect(traceRow.tag_revision).toBe(0);
    expect(api.refreshCells).not.toHaveBeenCalled();
  });

  it("updates every root of the trace with its own source tags", () => {
    // Two roots share the trace assignment: one save advances both rows'
    // revision, and each keeps its own SDK tags.
    const first = row("root-a", { sdk: ["sdk-a"] });
    const second = row("root-b", { sdk: ["sdk-b"] });
    const child = row("child", { parent: "root-a" });
    const api = gridApi([first, second, child]);
    const updated = applySavedEntityTags(api, {
      result: result(["sdk-a"], 4),
      entity: first,
      type: "span",
      gridKind: "span",
    });
    expect(updated).toBe(2);
    expect(first.tags).toEqual([{ name: "sdk-a" }, reviewed]);
    expect(second.tags).toEqual([{ name: "sdk-b" }, reviewed]);
    expect(second.source_tag_names).toEqual(["sdk-b"]);
    expect([first.tag_revision, second.tag_revision]).toEqual([4, 4]);
    expect(child.tag_revision).toBe(0);
  });

  it("matches a child span on its whole physical identity", () => {
    const saved = row("child", { parent: "root" });
    const otherHour = row("child", {
      parent: "root",
      tag_target: target("child", { start_hour: "2026-11-08T11:00:00+00:00" }),
    });
    const api = gridApi([saved, otherHour]);
    const updated = applySavedEntityTags(api, {
      result: result([]),
      entity: saved,
      type: "span",
      gridKind: "span",
    });
    expect(updated).toBe(1);
    expect(saved.tag_revision).toBe(1);
    expect(otherHour.tag_revision).toBe(0);
  });

  it("never moves a row back and never crosses projects", () => {
    const saved = row("child", { parent: "root", revision: 5 });
    const api = gridApi([saved]);
    expect(
      applySavedEntityTags(api, {
        result: result([], 3),
        entity: saved,
        type: "span",
        gridKind: "span",
      }),
    ).toBe(0);
    expect(
      applySavedEntityTags(api, {
        result: result([], 9),
        entity: {
          ...saved,
          tag_target: { ...saved.tag_target, project_id: "p2" },
        },
        type: "span",
        gridKind: "span",
      }),
    ).toBe(0);
    expect(saved.tag_revision).toBe(5);
  });

  it("does not treat a row without parent_span_id as a root", () => {
    const unknown = row("root-b");
    delete unknown.parent_span_id;
    const api = gridApi([unknown]);
    expect(
      applySavedEntityTags(api, {
        result: result([]),
        entity: { tag_target: target("root-a"), parent_span_id: "" },
        type: "span",
        gridKind: "span",
      }),
    ).toBe(0);
  });

  it("ignores a save without a revision or target, and a dead grid", () => {
    const api = gridApi([row("root")]);
    expect(applySavedEntityTags(api, { result: {}, entity: row("root") })).toBe(
      0,
    );
    expect(
      applySavedEntityTags(api, {
        result: result([]),
        entity: {},
        type: "trace",
      }),
    ).toBe(0);
    const dead = { ...gridApi([row("root")]), isDestroyed: () => true };
    expect(
      applySavedEntityTags(dead, {
        result: result([]),
        entity: row("root"),
        type: "trace",
        gridKind: "trace",
      }),
    ).toBe(0);
    expect(dead.forEachNode).not.toHaveBeenCalled();
  });
});
