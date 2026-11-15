import { describe, expect, it } from "vitest";
import {
  canEditEntityTags,
  latestTagState,
  rememberTagState,
  tagTargetKey,
} from "../entityTagState";

import type {
  EditableTagEntity,
  EntityTag,
  TagStateCache,
} from "../entityTagState";

type RootEntity = EditableTagEntity & { tags: EntityTag[] };

const root = (
  span: string,
  sdk: string,
  manual: string[] = [],
  revision = 0,
): RootEntity => ({
  parent_span_id: "",
  tag_target: {
    project_id: "project-1",
    trace_id: "trace-1",
    span_id: span,
    start_hour: "2026-11-08T10:00:00+00:00",
    observation_type: "span",
    service_name: "svc",
    expected_start_time: "2026-11-08T10:15:20.123456+00:00",
    expected_version: "1",
  },
  source_tag_names: [sdk],
  manual_tags: manual.map((name) => ({ name })),
  tags: [sdk, ...manual].map((name) => ({ name })),
  tag_revision: revision,
});

describe("entity tag assignment cache", () => {
  it("stores only shared manual tags and revision, not physical response metadata", () => {
    const cache: TagStateCache = new Map();
    const a = root("root-a", "sdk-a");
    const saved = root("root-a", "sdk-a", ["reviewed"], 1);
    expect(rememberTagState(cache, a, "span", saved)).toBe(true);
    expect(cache.get(tagTargetKey(a, "span"))).toEqual({
      manual_tags: [{ name: "reviewed" }],
      tag_revision: 1,
    });
    // A later in-place row update cannot mutate the assignment snapshot.
    saved.manual_tags[0].name = "changed";
    expect(latestTagState(a, cache).manual_tags).toEqual([
      { name: "reviewed" },
    ]);
  });

  it("keeps the newest manual watermark while using each root's latest SDK metadata", () => {
    const cache: TagStateCache = new Map();
    const a = root("root-a", "sdk-a");
    const b = root("root-b", "sdk-b", ["newer"], 4);
    rememberTagState(cache, b, "span");
    expect(
      rememberTagState(cache, a, "span", root("root-a", "sdk-a", ["older"], 3)),
    ).toBe(false);
    expect(latestTagState(a, cache).tags).toEqual([
      { name: "sdk-a" },
      { name: "newer" },
    ]);
    // Same revision can carry refreshed telemetry. Never use cached SDK state.
    expect(
      latestTagState({ ...b, source_tag_names: ["sdk-b-new"] }, cache).tags,
    ).toEqual([{ name: "sdk-b-new" }, { name: "newer" }]);
  });

  it("does not treat missing or truncated source metadata as an empty source set", () => {
    const cache: TagStateCache = new Map();
    const a = root("root-a", "sdk-a");
    rememberTagState(
      cache,
      a,
      "span",
      root("root-a", "sdk-a", ["reviewed"], 1),
    );
    const b = { ...root("root-b", "sdk-b"), source_tag_names: '["sdk-b"…' };
    expect(latestTagState(b, cache)).toBe(b);
    expect(canEditEntityTags(b)).toBe(false);
  });

  it("retains explicit manual/source overlaps and their manual colour", () => {
    const entity = root("root-a", "sdk-a", ["sdk-a"]);
    entity.manual_tags[0].color = "#123456";
    expect(latestTagState(entity).tags).toEqual([
      { name: "sdk-a", color: "#123456" },
    ]);
  });

  it("separates child physical identities, projects, and unknown parentage", () => {
    const cache: TagStateCache = new Map();
    const a = root("root-a", "sdk-a");
    rememberTagState(
      cache,
      a,
      "span",
      root("root-a", "sdk-a", ["reviewed"], 1),
    );
    const child = { ...root("child", "sdk-child"), parent_span_id: "root-a" };
    const otherProject = {
      ...a,
      tag_target: { ...a.tag_target, project_id: "other" },
    };
    const unknown = { ...a };
    delete unknown.parent_span_id;
    [child, otherProject, unknown].forEach((entity) => {
      expect(latestTagState(entity, cache).manual_tags).toEqual([]);
    });
    const otherHour = {
      ...child,
      tag_target: {
        ...child.tag_target,
        start_hour: "2026-11-08T11:00:00+00:00",
      },
    };
    expect(tagTargetKey(child)).not.toBe(tagTargetKey(otherHour));
  });
});
