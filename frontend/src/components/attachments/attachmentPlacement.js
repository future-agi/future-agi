import { parentSchema, spanAttachmentSchema } from "./contract";

// Pure helpers over the authorized per-span projection. They never derive a
// media location from span input/output or attributes; only the resolver can.

const MAX_FRAMES = 5000;

/** Valid attachment statuses on one observation span; invalid rows are dropped. */
export function readSpanAttachments(span) {
  const rows = Array.isArray(span?.attachments)
    ? span.attachments.slice(0, 64)
    : [];
  return rows.flatMap((row) => {
    const parsed = spanAttachmentSchema.safeParse(row);
    return parsed.success ? [parsed.data] : [];
  });
}

/** The exact resolver parent of an observation span, or null. */
export function spanParent(span) {
  const parsed = parentSchema.safeParse({
    trace_id: typeof span?.trace === "string" ? span.trace : span?.trace_id,
    span_id: span?.id,
  });
  return parsed.success ? parsed.data : null;
}

/** One rendering kind per reference. Screenshots belong to the filmstrip. */
export function attachmentKind(reference) {
  if (reference.modality === "image")
    return reference.purpose === "computer_screenshot" ? "screenshot" : "image";
  if (reference.modality === "audio" || reference.modality === "video")
    return "media";
  return "file";
}

const directionLabels = { input: "Input", output: "Output", mixed: "Mixed" };

/** Where the attachment sits in the span's messages, from its reference only. */
export function positionLabel(reference) {
  const parts = [
    reference.role === "tool"
      ? "Tool result"
      : directionLabels[reference.direction],
  ];
  if (reference.message_index !== undefined)
    parts.push(`message ${reference.message_index + 1}`);
  if (reference.part_index !== undefined)
    parts.push(`part ${reference.part_index + 1}`);
  if (reference.tool_call_id) parts.push(`call ${reference.tool_call_id}`);
  return parts.join(" · ");
}

const positionOrder = (reference) => [
  reference.direction === "output" ? 1 : 0,
  reference.message_index ?? 65536,
  reference.part_index ?? 65536,
];
function compareOrder(left, right) {
  for (let i = 0; i < left.length; i += 1)
    if (left[i] !== right[i]) return left[i] - right[i];
  return 0;
}

/** Group a span's non-screenshot attachments by message position, in order. */
export function groupByPosition(items) {
  const groups = new Map();
  items
    .map((item, index) => ({ item, index }))
    .sort(
      (a, b) =>
        compareOrder(
          positionOrder(a.item.reference),
          positionOrder(b.item.reference),
        ) || a.index - b.index,
    )
    .forEach(({ item }) => {
      const label = positionLabel(item.reference);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(item);
    });
  return [...groups.entries()].map(([label, groupItems]) => ({
    label,
    items: groupItems,
  }));
}

/**
 * Chronological computer-use screenshots across a trace tree.
 *
 * Order: span start time, then message/part position, then capture order.
 * Distinct occurrences with identical pixels stay distinct frames; the same
 * occurrence listed twice on one span is shown once.
 */
export function collectScreenshots(entries) {
  const frames = [];
  const seen = new Set();
  const stack = Array.isArray(entries) ? [...entries].reverse() : [];
  let order = 0;
  while (stack.length && frames.length < MAX_FRAMES) {
    const entry = stack.pop();
    const span = entry?.observation_span || {};
    const parent = spanParent(span);
    if (parent) {
      for (const item of readSpanAttachments(span)) {
        if (attachmentKind(item.reference) !== "screenshot") continue;
        const key = `${parent.span_id}/${item.reference.occurrence_id}`;
        if (seen.has(key)) continue;
        seen.add(key);
        frames.push({
          key,
          item,
          parent,
          spanName: typeof span.name === "string" ? span.name : "",
          startTime: Date.parse(span.start_time) || 0,
          order: (order += 1),
        });
      }
    }
    if (Array.isArray(entry?.children))
      for (let i = entry.children.length - 1; i >= 0; i -= 1)
        stack.push(entry.children[i]);
  }
  return frames.sort(
    (a, b) =>
      a.startTime - b.startTime ||
      compareOrder(
        positionOrder(a.item.reference),
        positionOrder(b.item.reference),
      ) ||
      a.order - b.order,
  );
}

/** Coarse, existence-safe text for an attachment that cannot be shown. */
export const unavailableLabels = {
  pending: "Waiting for the trace",
  pending_upload: "Uploading",
  importing: "Importing",
  validating: "Validating",
  partial: "Incomplete capture",
  failed: "Could not be prepared",
  not_found: "Unavailable",
  expired: "Expired",
  deleted: "Deleted",
  unavailable: "Unavailable",
};
