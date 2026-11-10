import { useCallback, useRef } from "react";
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

export default function useSaveEntityTags() {
  const saved = useRef(new Map());
  return useCallback(async ({ entity, tags, type = "trace" }) => {
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
    const key = JSON.stringify(target);
    const previous = saved.current.get(key);
    const latest =
      previous?.tag_revision > entity.tag_revision ? previous : entity;
    const manualNames = new Set(
      (latest.manual_tags || []).map((tag) => tag.name),
    );
    const body = {
      ...target,
      expected_tag_revision: latest.tag_revision,
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
    const result = response.data.result;
    // A popover can stay open for several saves before its grid refetches.
    // Carry the acknowledged revision forward; never retry a stale write.
    saved.current.set(key, result);
    return result;
  }, []);
}
