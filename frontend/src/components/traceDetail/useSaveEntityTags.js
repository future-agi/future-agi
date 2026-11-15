import { useCallback, useEffect, useState } from "react";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import {
  TAG_TARGET_FIELDS,
  canEditEntityTags,
  hasTagMetadata,
  isSourceTag,
  latestTagState,
  rememberTagState,
} from "./entityTagState";

export {
  canEditEntityTags,
  hasTagTarget,
  isSourceTag,
  latestTagState,
  rememberTagState,
  tagPhysicalKey,
  tagTargetKey,
} from "./entityTagState";

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

/**
 * Until the selected entity catches up, use the latest known shared manual
 * assignment, recombined with this physical entity's own SDK/source tags.
 */
export function useLatestEntityTags(entity, tags, type = "span") {
  const [acknowledged, setAcknowledged] = useState(() => new Map());
  const remember = useCallback((state, savedEntity, savedType) => {
    setAcknowledged((current) => {
      const next = new Map(current);
      return rememberTagState(next, savedEntity, savedType, state)
        ? next
        : current;
    });
  }, []);
  useEffect(() => {
    if (canEditEntityTags(entity)) remember(entity, entity, type);
  }, [entity, entity?.tag_revision, type, remember]);
  const base = latestTagState(entity, acknowledged, type);
  const confirm = useCallback(
    (result, savedEntity) => remember(result, savedEntity, type),
    [remember, type],
  );
  return { tags: base === entity ? tags : base.tags, base, confirm };
}

/**
 * `tags` is the complete new list built from `base`. Pair that list with its
 * original revision: never upgrade a stale edit to a newer revision, since
 * only the backend's 409 prevents it from dropping a concurrent assignment.
 */
export default function useSaveEntityTags() {
  return useCallback(async ({ entity, tags, type = "trace", base }) => {
    const state = base ?? entity;
    if (
      !canEditEntityTags(entity) ||
      !hasTagMetadata(state) ||
      !Array.isArray(tags)
    ) {
      const error = new Error("Missing complete tag editing state");
      error.code = "tag_scope_required";
      throw error;
    }
    // Preserve timestamp and UInt64 identity strings without coercion.
    const target = Object.fromEntries(
      TAG_TARGET_FIELDS.map((field) => [field, entity.tag_target[field]]),
    );
    const manualNames = new Set(state.manual_tags.map((tag) => tag.name));
    const body = {
      ...target,
      expected_tag_revision: state.tag_revision,
      // Sources belong to this physical row, not the shared assignment cache.
      tags: tags.filter(
        (tag) => !isSourceTag(entity, tag.name) || manualNames.has(tag.name),
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
