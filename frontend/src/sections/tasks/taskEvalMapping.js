// Native mapping surface defined by _SPAN/_TRACE/_SESSION_PUBLIC_FIELDS in
// tracer/utils/eval.py. A row response is broader than this execution contract:
// its identity/transport fields are not evaluator inputs. OTel attributes stay
// open-ended, including attributes that happen to share a native field's name.
const SPAN_FIELDS = new Set([
  "latency_ms",
  "prompt_tokens",
  "completion_tokens",
  "total_tokens",
  "cost",
  "response_time",
  "model",
  "name",
  "observation_type",
  "status",
  "status_message",
  "provider",
  "input",
  "output",
]);
const TRACE_FIELDS = new Set([
  "input",
  "output",
  "name",
  "error",
  "tags",
  "metadata",
  "external_id",
]);
const SESSION_FIELDS = new Set(["name", "bookmarked"]);
const IDENTITY_FIELDS = new Set([
  "id",
  "span_id",
  "trace_id",
  "project_id",
  "org_id",
  "parent_span_id",
]);
const owns = (object, key) =>
  Object.prototype.hasOwnProperty.call(object || {}, key);

export function isUnsupportedTaskNativePath(
  path,
  rowType,
  row,
  attributeFields = [],
) {
  if (typeof path !== "string" || !path || attributeFields.includes(path))
    return false;
  const type = rowType.toLowerCase();
  const [head, ...tail] = path.split(".");
  if (type === "session" || type === "sessions") {
    if (head === "traces" && /^\d+$/.test(tail[0]) && tail.length > 1) {
      return isUnsupportedTaskNativePath(
        tail.slice(1).join("."),
        "traces",
        row?.traces?.[Number(tail[0])],
      );
    }
    return (
      head !== "traces" &&
      !SESSION_FIELDS.has(head) &&
      (owns(row, head) || IDENTITY_FIELDS.has(head))
    );
  }
  if (type === "trace" || type === "traces") {
    if (head === "spans" && /^\d+$/.test(tail[0]) && tail.length > 1) {
      return isUnsupportedTaskNativePath(
        tail.slice(1).join("."),
        "spans",
        row?.spans?.[Number(tail[0])],
      );
    }
    // Explicit filter-bound span paths are resolved by the backend, not by
    // the one-row preview. Do not reject them using a partial row's shape.
    if (head === "filter_spans" || head === "spans") return false;
    return (
      !TRACE_FIELDS.has(head) && (owns(row, head) || IDENTITY_FIELDS.has(head))
    );
  }
  // Explicit attribute-bag paths are supported by nested span resolvers;
  // existing root mappings also retain their legacy normalization in the picker.
  if (head === "span_attributes") return false;
  const attributes = row?.span_attributes;
  if (
    attributeFields.includes(path) ||
    owns(attributes, path) ||
    owns(attributes, `${path}.value`) ||
    owns(attributes, head)
  )
    return false;
  // Voice call response fields can be backed by the resolver's raw-log fallback.
  if (type === "voicecalls" || type === "voice") return false;
  return (
    !SPAN_FIELDS.has(head) && (owns(row, head) || IDENTITY_FIELDS.has(head))
  );
}

export function taskNativeMappingMessage(path) {
  return `Native field "${path}" is not supported for task evaluation. Map this variable to a supported field such as input, output or a span attribute.`;
}
