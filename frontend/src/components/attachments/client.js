import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import {
  gatewaySessionId,
  occurrencePageSchema,
  parentSchema,
  resolveItemSchema,
  resolveResponseSchema,
  uuid,
} from "./contract";
import {
  MEDIA_SESSION_HEADER,
  acquireMediaSession,
  captureMediaContext,
  discardMediaSession,
  isCurrentMediaContext,
  pinnedRequestConfig,
} from "./mediaSession";

const requestConfig = (signal) => ({
  // Same-origin reads; deployments must proxy these paths.
  baseURL: window.location.origin,
  signal,
  withCredentials: true,
  xsrfCookieName: "csrftoken",
  xsrfHeaderName: "X-CSRFToken",
  headers: { "Cache-Control": "no-store" },
});

export class AttachmentUnavailable extends Error {
  constructor() {
    super("Attachment unavailable");
    this.name = "AttachmentUnavailable";
  }
}

// One exchange-to-resolve pair under ONE captured bearer + organization: the
// resolve names exactly the handle that context's exchange returned, and each
// granted path must carry that same session and the item's own occurrence.
async function resolveUnder(captured, projectId, items, body, signal, fresh) {
  const session = await acquireMediaSession(captured, signal, { fresh });
  if (!isCurrentMediaContext(captured)) throw new AttachmentUnavailable();
  let response;
  try {
    response = await axios.post(
      apiPath("/tracer/v1/projects/{project_id}/attachments/resolve", {
        project_id: projectId,
      }),
      body,
      pinnedRequestConfig(captured, signal, {
        [MEDIA_SESSION_HEADER]: session.handle,
      }),
    );
  } catch (error) {
    // Stop reusing a session whose resolve failed (dead, foreign, refused);
    // the caller's own abort is not evidence against it.
    if (!signal?.aborted) discardMediaSession(session);
    throw error;
  }
  // A reply for a replaced bearer/organization is discarded, never applied.
  if (!isCurrentMediaContext(captured)) throw new AttachmentUnavailable();
  const parsed = resolveResponseSchema.safeParse(response.data);
  // Contract order is significant for channel requests on the same occurrence.
  if (
    !parsed.success ||
    parsed.data.items.length !== items.length ||
    parsed.data.items.some(
      (item, i) =>
        item.occurrence_id !== items[i].occurrence_id ||
        (item.status === "ready" &&
          gatewaySessionId(item.gateway_path) !== session.handle),
    )
  ) {
    discardMediaSession(session);
    throw new AttachmentUnavailable();
  }
  for (const item of parsed.data.items) {
    if (item.status === "ready" && Date.parse(item.expires_at) <= Date.now())
      throw new AttachmentUnavailable();
  }
  return parsed.data.items;
}

// No global query cache: a grant belongs to this viewer/session and parent only.
// Neither transport errors nor response bodies are sent to browser telemetry.
// `fresh` asks for a new media session instead of reusing one, after the
// gateway refused media on the previous session (e.g. an evicted cookie).
export async function resolveAttachments(
  projectId,
  items,
  signal,
  { fresh = false } = {},
) {
  try {
    uuid.parse(projectId);
    if (!items.length || items.length > 64) throw new AttachmentUnavailable();
    const body = { items: items.map((item) => resolveItemSchema.parse(item)) };
    if (window.location.pathname.startsWith("/shared/"))
      throw new AttachmentUnavailable();
    for (let attempt = 0; ; attempt += 1) {
      const captured = captureMediaContext();
      // Exchange and resolve both require an explicit application bearer.
      if (!captured) throw new AttachmentUnavailable();
      try {
        return await resolveUnder(
          captured,
          projectId,
          items,
          body,
          signal,
          // A refused session belongs to the first context only.
          fresh && attempt === 0,
        );
      } catch (error) {
        // The bearer or organization changed mid-pair (e.g. a token refresh
        // triggered by this very exchange): drop that pair's result and start
        // once more under the new context, never mixing the two.
        if (signal?.aborted || attempt > 0 || isCurrentMediaContext(captured))
          throw error;
      }
    }
  } catch {
    throw new AttachmentUnavailable();
  }
}

export async function listAttachments(projectId, parent, cursor, signal) {
  try {
    uuid.parse(projectId);
    parentSchema.parse(parent);
    if (cursor && !/^[a-zA-Z0-9_-]{1,128}$/.test(cursor))
      throw new AttachmentUnavailable();
    if (window.location.pathname.startsWith("/shared/"))
      throw new AttachmentUnavailable();
    const response = await axios.get(
      apiPath("/tracer/v1/projects/{project_id}/attachments/occurrences", {
        project_id: projectId,
      }),
      {
        ...requestConfig(signal),
        params: { ...parent, ...(cursor ? { cursor } : {}), limit: 25 },
      },
    );
    const page = occurrencePageSchema.parse(response.data);
    // Defend against a stale/incorrectly scoped response, including pagination.
    const trace = parent.trace_id.replaceAll("-", "");
    if (
      page.items.some(
        (item) =>
          item.scope.project_id !== projectId ||
          item.parent.trace_id.replaceAll("-", "") !== trace ||
          item.parent.span_id !== parent.span_id,
      )
    )
      throw new AttachmentUnavailable();
    return page;
  } catch {
    throw new AttachmentUnavailable();
  }
}
