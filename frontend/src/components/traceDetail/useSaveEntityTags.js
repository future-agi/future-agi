import { useCallback, useState } from "react";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

const TARGET_FIELDS = [
  "project_id",
  "trace_id",
  "span_id",
  "start_hour",
  "observation_type",
  "service_name",
  "expected_start_time",
  "expected_version",
];

export const tagUpdateErrorMessage = (error) => {
  const status = error?.statusCode ?? error?.response?.status;
  if (status === 403) {
    return "You need write access to this workspace to edit tags. Ask a workspace admin for access.";
  }
  if (status === 404) {
    return "This trace or span is no longer available. Refresh the list before editing tags.";
  }
  if (status === 409) {
    return "This trace, span, or its tags changed. Refresh and review the latest tags before trying again.";
  }
  if (status === 503) {
    return "Tags could not be saved because the service is temporarily unavailable. Try again shortly.";
  }
  if (error?.code === "tag_scope_required") {
    return "Refresh this trace or span to load its tag editing details, then try again.";
  }
  return "Failed to update tags. Please try again.";
};

// SDK/source tags are immutable here. Never silently turn them into manual
// assignments just because the editor displays the effective union.
export const isSourceTag = (entity, name) =>
  (entity?.source_tag_names || []).includes(name);

export const hasTagTarget = (entity) =>
  TARGET_FIELDS.every(
    (field) => typeof entity?.tag_target?.[field] === "string",
  ) &&
  Number.isSafeInteger(entity?.tag_revision) &&
  entity.tag_revision >= 0;

// A trace row and every root span share one tag assignment; non-root spans
// are keyed by their physical target (as in applySavedEntityTags).
export const tagTargetKey = (entity, type) => {
  const target = entity?.tag_target;
  if (!target) return null;
  const isRoot =
    Object.prototype.hasOwnProperty.call(entity, "parent_span_id") &&
    (entity.parent_span_id == null || entity.parent_span_id === "");
  if (type === "trace" || isRoot) {
    return JSON.stringify(["trace", target.project_id, target.trace_id]);
  }
  return JSON.stringify([
    "span",
    JSON.stringify(
      [
        "project_id",
        "trace_id",
        "span_id",
        "start_hour",
        "observation_type",
        "service_name",
      ].map((field) => target[field] ?? null),
    ),
  ]);
};

// The newest tag state an editor knows for an entity: the entity as loaded,
// or a later acknowledged save of the same target (a save result has tags,
// manual_tags, source_tag_names and tag_revision). `acknowledged` maps
// tagTargetKey -> the last acknowledged result for that target, so an editor
// that moves between entities keeps each one's save. An editor builds its
// next tag list from this state and sends it as the save's base, so the list
// and the expected revision always come from the same state.
export const latestTagState = (entity, acknowledged, type) => {
  const key = tagTargetKey(entity, type);
  const result = key === null ? undefined : acknowledged?.get(key);
  if (
    !result ||
    !Number.isSafeInteger(result.tag_revision) ||
    !Number.isSafeInteger(entity?.tag_revision) ||
    result.tag_revision <= entity.tag_revision
  ) {
    return entity;
  }
  return result;
};

/**
 * Tags an inline editor shows, kept at the last acknowledged save until the
 * entity it was given catches up (a refetch can land after the next edit).
 *
 * @returns {{ tags, base, confirm }} the tags to show, the state they come
 *   from (pass it to the save as `base`), and confirm(result, savedEntity)
 *   to call when a save is acknowledged.
 */
export function useLatestEntityTags(entity, tags) {
  const [acknowledged, setAcknowledged] = useState(() => new Map());
  const base = latestTagState(entity, acknowledged);
  const confirm = useCallback(
    (result, savedEntity) =>
      setAcknowledged((current) =>
        new Map(current).set(tagTargetKey(savedEntity), result),
      ),
    [],
  );
  return { tags: base === entity ? tags : base.tags || [], base, confirm };
}

/**
 * Save an entity's tags.
 *
 * `tags` is the complete new list, built by the caller from `base`: the
 * entity as loaded (the default) or a later acknowledged save of it. The save
 * expects `base`'s revision, so a list built from stale data is refused (409)
 * instead of silently dropping a newer tag.
 */
export default function useSaveEntityTags() {
  return useCallback(async ({ entity, tags, type = "trace", base }) => {
    if (!hasTagTarget(entity)) {
      const error = new Error("Missing tag target");
      error.code = "tag_scope_required";
      throw error;
    }
    // Use the server-provided physical identity verbatim: formatting timestamps
    // or coercing UInt64 versions through Number loses selection precision.
    const target = Object.fromEntries(
      TARGET_FIELDS.map((field) => [field, entity.tag_target[field]]),
    );
    const state = Number.isSafeInteger(base?.tag_revision) ? base : entity;
    const manualNames = new Set(
      (state.manual_tags || []).map((tag) => tag.name),
    );
    const sourceState = state.source_tag_names ? state : entity;
    const body = {
      ...target,
      expected_tag_revision: state.tag_revision,
      tags: tags.filter(
        (tag) =>
          !isSourceTag(sourceState, tag.name) || manualNames.has(tag.name),
      ),
    };
    const response =
      type === "span"
        ? await axios.post(
            apiPath("/tracer/observation-span/update-tags/"),
            body,
          )
        : await axios.patch(
            apiPath("/tracer/trace/{id}/tags/", { id: target.trace_id }),
            body,
          );
    return response.data.result;
  }, []);
}
