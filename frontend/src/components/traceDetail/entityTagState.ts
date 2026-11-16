export const TAG_TARGET_FIELDS = [
  "project_id",
  "trace_id",
  "span_id",
  "start_hour",
  "observation_type",
  "service_name",
  "expected_start_time",
  "expected_version",
] as const;

export type TagTarget = Record<(typeof TAG_TARGET_FIELDS)[number], string>;

export interface EntityTag {
  name: string;
  // Preserve payload fields (including colour) without granting them authority.
  [field: string]: unknown;
}

export interface ManualTagState {
  manual_tags: EntityTag[];
  tag_revision: number;
  tag_assignment_exists: true;
}

// List previews and incomplete responses are deliberately not trusted metadata.
export interface EntityTagInput {
  tag_target?: Partial<Record<keyof TagTarget, unknown>> | null;
  parent_span_id?: unknown;
  tag_revision?: unknown;
  tag_assignment_exists?: unknown;
  manual_tags?: unknown;
  source_tag_names?: unknown;
  tags?: unknown;
}

export interface TagMetadata extends EntityTagInput {
  // Complete replacement set: saved assignment, or validated legacy labels.
  manual_tags: EntityTag[];
  tag_revision: number;
  source_tag_names: string[];
  tag_assignment_exists: boolean;
}

export type EditableTagEntity = TagMetadata & { tag_target: TagTarget };
export type TagStateCache = Map<string | null, ManualTagState>;
type MaybeEntity = EntityTagInput | null | undefined;

const hasRevision = (
  state: MaybeEntity,
): state is EntityTagInput & { tag_revision: number } =>
  typeof state?.tag_revision === "number" &&
  Number.isSafeInteger(state.tag_revision) &&
  state.tag_revision >= 0;

export const hasTagTarget = (
  entity: MaybeEntity,
): entity is EntityTagInput & { tag_target: TagTarget; tag_revision: number } =>
  TAG_TARGET_FIELDS.every(
    (field) => typeof entity?.tag_target?.[field] === "string",
  ) && hasRevision(entity);

const hasTagName = (tag: unknown): tag is EntityTag => {
  const name = (tag as { name?: unknown } | null | undefined)?.name;
  return typeof name === "string" && name.length > 0;
};

const hasManualTags = (
  state: MaybeEntity,
): state is EntityTagInput & { manual_tags: EntityTag[] } =>
  state != null &&
  Array.isArray(state.manual_tags) &&
  state.manual_tags.every(hasTagName);

export const hasTagMetadata = (state: MaybeEntity): state is TagMetadata =>
  hasRevision(state) &&
  typeof state.tag_assignment_exists === "boolean" &&
  hasManualTags(state) &&
  Array.isArray(state?.source_tag_names) &&
  state.source_tag_names.every(
    (name: unknown) => typeof name === "string" && name.length > 0,
  );

// Display previews (including `tags`) are not replacement-write authority.
// Missing or bounded/truncated metadata must never be interpreted as empty.
export const canEditEntityTags = (
  entity: MaybeEntity,
): entity is EditableTagEntity =>
  hasTagTarget(entity) && hasTagMetadata(entity);

export const isSourceTag = (entity: MaybeEntity, name: string) =>
  entity != null &&
  Array.isArray(entity.source_tag_names) &&
  entity.source_tag_names.includes(name);

export const tagPhysicalKey = (entity: MaybeEntity) =>
  JSON.stringify(TAG_TARGET_FIELDS.map((field) => entity?.tag_target?.[field]));

// A trace row and every root span share only a manual assignment. Non-root
// assignments belong to their physical identity, not just the span id.
export const tagTargetKey = (entity: MaybeEntity, type?: string) => {
  const target = entity?.tag_target;
  if (!target || typeof target !== "object") return null;
  const isRoot =
    Object.prototype.hasOwnProperty.call(entity, "parent_span_id") &&
    (entity?.parent_span_id == null || entity.parent_span_id === "");
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
  entity: TagMetadata,
  manualTags: EntityTag[] = entity?.manual_tags,
): EntityTag[] => {
  const sourceNames = new Set(entity.source_tag_names);
  const tags = new Map<string, EntityTag>();
  if (Array.isArray(entity.tags)) {
    entity.tags.forEach((tag: unknown) => {
      if (hasTagName(tag) && sourceNames.has(tag.name))
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
export const rememberTagState = (
  cache: TagStateCache,
  entity: MaybeEntity,
  type?: string,
  state: MaybeEntity = entity,
) => {
  const key = tagTargetKey(entity, type);
  if (
    key === null ||
    !hasRevision(state) ||
    !hasManualTags(state) ||
    state.tag_assignment_exists !== true
  )
    return false;
  const previous = cache.get(key);
  if (previous && previous.tag_revision >= state.tag_revision) return false;
  cache.set(key, {
    manual_tags: state.manual_tags.map((tag) => ({ ...tag })),
    tag_revision: state.tag_revision,
    tag_assignment_exists: true,
  });
  return true;
};

export const latestTagState = <T extends MaybeEntity>(
  entity: T,
  acknowledged?: TagStateCache,
  type?: string,
) => {
  if (!hasTagMetadata(entity)) return entity;
  const result = acknowledged?.get(tagTargetKey(entity, type));
  const shared =
    result && result.tag_revision > entity.tag_revision ? result : entity;
  return {
    ...entity,
    manual_tags: shared.manual_tags,
    tag_revision: shared.tag_revision,
    tag_assignment_exists: shared.tag_assignment_exists,
    tags: effectiveEntityTags(entity, shared.manual_tags),
  };
};
