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

// Message role strings as providers write them -> F01 Reference.role.
const F01_ROLES = {
  user: "user",
  human: "user",
  assistant: "assistant",
  model: "assistant",
  ai: "assistant",
  tool: "tool",
  function: "tool",
  system: "system",
  developer: "system",
};

/**
 * Images that belong INSIDE one rendered chat message, in part order.
 *
 * Placement is taken only from the occurrence's own reference: same direction,
 * same message_index and, when the reference names a role, the same role as
 * the rendered message. The role check catches index skew between the SDK's
 * message array and the messages the trace view parsed; anything that fails a
 * check stays in the span's Attachments list, never on a wrong message.
 * Screenshots stay in the filmstrip and `mixed` direction is never inlined.
 */
export function inlineImagesFor(items, { direction, messageIndex, role }) {
  if (direction !== "input" && direction !== "output") return [];
  if (!Number.isInteger(messageIndex) || messageIndex < 0) return [];
  const messageRole =
    typeof role === "string" ? F01_ROLES[role.toLowerCase()] : undefined;
  return items
    .map((item, index) => ({ item, index }))
    .filter(({ item: { reference } }) => {
      if (attachmentKind(reference) !== "image") return false;
      if (reference.direction !== direction) return false;
      if (reference.message_index !== messageIndex) return false;
      return reference.role === undefined || reference.role === messageRole;
    })
    .sort(
      (a, b) =>
        (a.item.reference.part_index ?? 65536) -
          (b.item.reference.part_index ?? 65536) || a.index - b.index,
    )
    .map(({ item }) => item);
}

/**
 * The step a screenshot belongs to: its conversation turn when the reference
 * names both ids, else its own span. Message positions only mean something
 * within one step, so they are never compared across steps.
 */
function stepKey(frame) {
  const { conversation_id: conversation, turn_id: turn } = frame.item.reference;
  return conversation && turn
    ? `turn:${conversation}:${turn}`
    : `span:${frame.parent.span_id}`;
}

/**
 * Causal computer-use screenshots across a trace tree.
 *
 * Steps (a conversation turn, else a span) run in the order they started;
 * within a step frames follow their placement (input before output, then
 * message and part), then span start time, then capture order. A tool span's
 * screenshot that carries no message position is therefore never pushed
 * behind later steps that do. Distinct occurrences with identical pixels stay
 * distinct frames; the same occurrence listed twice on one span is shown once.
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
  const steps = new Map();
  for (const frame of frames) {
    const step = stepKey(frame);
    const first = steps.get(step);
    if (
      !first ||
      frame.startTime < first.startTime ||
      (frame.startTime === first.startTime && frame.order < first.order)
    )
      steps.set(step, { startTime: frame.startTime, order: frame.order });
  }
  const stepOf = (frame) => steps.get(stepKey(frame));
  return frames.sort((a, b) => {
    const left = stepOf(a);
    const right = stepOf(b);
    return (
      left.startTime - right.startTime ||
      left.order - right.order ||
      compareOrder(
        positionOrder(a.item.reference),
        positionOrder(b.item.reference),
      ) ||
      a.startTime - b.startTime ||
      a.order - b.order
    );
  });
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
