import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import {
  occurrencePageSchema,
  parentSchema,
  resolveItemSchema,
  resolveResponseSchema,
  uuid,
} from "./contract";

const requestConfig = (signal) => ({
  // The media-session cookie must be established on the app's own origin.
  // Deployments must proxy these paths; never switch to provider/object URLs.
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

// No global query cache: a grant belongs to this viewer/session and parent only.
// Neither transport errors nor response bodies are sent to browser telemetry.
export async function resolveAttachments(projectId, items, signal) {
  try {
    uuid.parse(projectId);
    if (!items.length || items.length > 64) throw new AttachmentUnavailable();
    const body = { items: items.map((item) => resolveItemSchema.parse(item)) };
    if (window.location.pathname.startsWith("/shared/"))
      throw new AttachmentUnavailable();
    const config = requestConfig(signal);
    // Separate narrow exchange: an application session plus a playback grant,
    // never a bearer URL alone. Reauthorize on every renewal; do not cache it.
    // The contract body is AttachmentEmptyRequest: `{}`, not an absent body,
    // which the development request-contract check rejects before sending.
    const session = await axios.post(
      apiPath("/tracer/v1/attachment-media-sessions"),
      {},
      config,
    );
    if (session.status !== 204) throw new AttachmentUnavailable();
    const response = await axios.post(
      apiPath("/tracer/v1/projects/{project_id}/attachments/resolve", {
        project_id: projectId,
      }),
      body,
      config,
    );
    const parsed = resolveResponseSchema.parse(response.data);
    // Contract order is significant for channel requests on the same occurrence.
    if (
      parsed.items.length !== items.length ||
      parsed.items.some(
        (item, i) => item.occurrence_id !== items[i].occurrence_id,
      )
    )
      throw new AttachmentUnavailable();
    for (const item of parsed.items) {
      if (item.status === "ready" && Date.parse(item.expires_at) <= Date.now())
        throw new AttachmentUnavailable();
    }
    return parsed.items;
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
