export const TAG_TARGET_FIELDS = [
  "project_id",
  "trace_id",
  "span_id",
  "start_hour",
  "observation_type",
  "service_name",
  "expected_start_time",
  "expected_version",
];

const hasRevision = (state) =>
  Number.isSafeInteger(state?.tag_revision) && state.tag_revision >= 0;

export const hasTagTarget = (entity) =>
  TAG_TARGET_FIELDS.every(
    (field) => typeof entity?.tag_target?.[field] === "string",
  ) && hasRevision(entity);

const hasManualTags = (state) =>
  Array.isArray(state?.manual_tags) &&
  state.manual_tags.every(
    (tag) => tag && typeof tag.name === "string" && tag.name.length > 0,
  );

export const hasTagMetadata = (state) =>
  hasRevision(state) &&
  hasManualTags(state) &&
  Array.isArray(state?.source_tag_names) &&
  state.source_tag_names.every(
    (name) => typeof name === "string" && name.length > 0,
  );

// Display previews (including `tags`) are not replacement-write authority.
// Missing or bounded/truncated metadata must never be interpreted as empty.
export const canEditEntityTags = (entity) =>
  hasTagTarget(entity) && hasTagMetadata(entity);

export const isSourceTag = (entity, name) =>
  Array.isArray(entity?.source_tag_names) &&
  entity.source_tag_names.includes(name);

export const tagPhysicalKey = (entity) =>
  JSON.stringify(TAG_TARGET_FIELDS.map((field) => entity?.tag_target?.[field]));

// A trace row and every root span share only a manual assignment. Non-root
// assignments belong to their physical identity, not just the span id.
export const tagTargetKey = (entity, type) => {
  const target = entity?.tag_target;
  if (!target || typeof target !== "object") return null;
  const isRoot =
    Object.prototype.hasOwnProperty.call(entity, "parent_span_id") &&
    (entity.parent_span_id == null || entity.parent_span_id === "");
  if (type === "trace" || isRoot) {
    return JSON.stringify(["trace", target.project_id, target.trace_id]);
  }
  return JSON.stringify([
    "span",
    JSON.stringify(
      TAG_TARGET_FIELDS.slice(0, 6).map((field) => target[field] ?? null),
    ),
  ]);
};

// Preserve source colours when the display array is available, but derive
// membership from complete source/manual metadata, never from a preview.
// Shared by editors and sibling-root grid updates.
export const effectiveEntityTags = (
  entity,
  manualTags = entity?.manual_tags,
) => {
  const sourceNames = new Set(entity.source_tag_names);
  const tags = new Map();
  if (Array.isArray(entity.tags)) {
    entity.tags.forEach((tag) => {
      if (tag?.name && sourceNames.has(tag.name))
        tags.set(tag.name, { ...tag });
    });
  }
  sourceNames.forEach((name) => {
    if (!tags.has(name)) tags.set(name, { name });
  });
  manualTags.forEach((tag) => {
    tags.set(tag.name, { ...(tags.get(tag.name) || {}), ...tag });
  });
  return [...tags.values()];
};

// Store only assignment-wide state. SDK tags and physical target identity
// must always come from the selected row, even when a sibling's save arrives.
// Refetches also advance this watermark; delayed responses cannot undo them.
export const rememberTagState = (cache, entity, type, state = entity) => {
  const key = tagTargetKey(entity, type);
  if (key === null || !hasRevision(state) || !hasManualTags(state))
    return false;
  const previous = cache.get(key);
  if (previous && previous.tag_revision >= state.tag_revision) return false;
  cache.set(key, {
    manual_tags: state.manual_tags.map((tag) => ({ ...tag })),
    tag_revision: state.tag_revision,
  });
  return true;
};

export const latestTagState = (entity, acknowledged, type) => {
  if (!hasTagMetadata(entity)) return entity;
  const result = acknowledged?.get(tagTargetKey(entity, type));
  const shared =
    result && result.tag_revision > entity.tag_revision ? result : entity;
  return {
    ...entity,
    manual_tags: shared.manual_tags,
    tag_revision: shared.tag_revision,
    tags: effectiveEntityTags(entity, shared.manual_tags),
  };
};
