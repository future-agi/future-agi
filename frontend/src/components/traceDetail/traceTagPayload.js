import { tagName } from "./tagUtils";

/**
 * Payload for PATCH /tracer/trace/{id}/tags/ (calls, chats and traces).
 *
 * The UI keeps tags as `{ name, color }` objects, but the trace endpoint
 * stores a list of non-blank tag names (`TraceTagsUpdate`), so only the names
 * are sent. Trace tag colours are not stored: after a refetch the UI derives
 * them from the name again. Span tags go to a different endpoint that stores
 * the objects, colours included, so span payloads must not use this.
 */
export const serializeTraceTags = (traceTags) =>
  traceTags
    .map((tag) => String(tagName(tag) ?? ""))
    .filter((name) => name.trim() !== "");
