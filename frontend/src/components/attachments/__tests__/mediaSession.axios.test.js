// The app's REAL axios instance and its 401 refresh interceptor, with only the
// network replaced by an adapter double. The interceptor rewrites a failed
// request's Authorization and re-dispatches it; no attachment request may ever
// leave the browser carrying a media-session handle under a bearer other than
// the one whose exchange returned it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));

import axios from "src/utils/axios";
import { setSession } from "src/auth/context/jwt/utils";
import { resolveAttachments } from "../client";
import { resetMediaSessions } from "../mediaSession";
import { grant, occurrenceId, parent, projectId } from "./fixtures";

const organization = "50000000-0000-0000-0000-000000000005";
const handles = {
  "Bearer synthetic-access-a": "e0000000-0000-0000-0000-0000000000a1",
  "Bearer synthetic-access-b": "e0000000-0000-0000-0000-0000000000b2",
};
const [handleA, handleB] = Object.values(handles);
const itemsFor = (occurrence_id) => [
  { occurrence_id, parent, representation: "playable" },
];
const otherOccurrence = "20000000-0000-0000-0000-0000000000f2";

let sent;
let originalAdapter;
function network({ expired }) {
  sent = [];
  axios.defaults.adapter = async (config) => {
    const header = (name) => config.headers.get(name) ?? null;
    const request = {
      url: config.url,
      bearer: header("Authorization"),
      organization: header("X-Organization-Id"),
      handle: header("X-Attachment-Media-Session"),
      origin: header("Origin"),
    };
    sent.push(request);
    const reply = (status, data, headers = {}) => {
      const response = { status, statusText: "", data, headers, config };
      if (status >= 400)
        return Promise.reject({ message: `${status}`, config, response });
      return response;
    };
    if (config.url === "/accounts/token/refresh/")
      return reply(200, { access: "synthetic-access-b" });
    if (request.bearer === expired.bearer && expired.urls.test(config.url))
      return reply(401, { detail: "Token expired" });
    if (config.url === "/tracer/v1/attachment-media-sessions")
      return reply(204, "", {
        "x-attachment-media-session": handles[request.bearer],
        "x-attachment-media-expires-at": new Date(Date.now() + 300000)
          .toISOString()
          .replace("Z", "+00:00"),
      });
    const items = JSON.parse(config.data).items.map(({ occurrence_id }) => ({
      ...grant,
      occurrence_id,
      gateway_path: `/tracer/v1/attachment-media/${request.handle}/${occurrence_id}/g`,
    }));
    return reply(200, { items });
  };
}

beforeEach(() => {
  originalAdapter = axios.defaults.adapter;
  vi.spyOn(console, "warn").mockImplementation(() => {});
  localStorage.setItem("refreshToken", "synthetic-refresh");
  localStorage.setItem("rememberMe", "true");
  setSession("synthetic-access-a", organization);
  resetMediaSessions();
});
afterEach(() => {
  axios.defaults.adapter = originalAdapter;
  setSession(null);
  localStorage.clear();
  vi.restoreAllMocks();
});

const exchanges = (bearer) =>
  sent.filter(
    (r) =>
      r.url === "/tracer/v1/attachment-media-sessions" && r.bearer === bearer,
  );
const resolvesWith = (bearer, handle) =>
  sent.filter(
    (r) =>
      r.url.endsWith("/resolve") && r.bearer === bearer && r.handle === handle,
  );

describe("integration: media-session binding through the real axios interceptors", () => {
  it("never replays a resolve with its old handle under the refreshed bearer", async () => {
    network({
      expired: { bearer: "Bearer synthetic-access-a", urls: /\/resolve$/ },
    });
    // Two concurrent resolves: one refreshes, the other queues behind it.
    const [[first], [second]] = await Promise.all([
      resolveAttachments(projectId, itemsFor(occurrenceId)),
      resolveAttachments(projectId, itemsFor(otherOccurrence)),
    ]);
    expect(first.gateway_path).toContain(`/${handleB}/`);
    expect(second.gateway_path).toContain(`/${handleB}/`);
    expect(axios.defaults.headers.common.Authorization).toBe(
      "Bearer synthetic-access-b",
    );
    // A's handle went out only under A; nothing paired it with B's bearer.
    expect(resolvesWith("Bearer synthetic-access-b", handleA)).toEqual([]);
    expect(resolvesWith("Bearer synthetic-access-a", handleA)).toHaveLength(2);
    expect(resolvesWith("Bearer synthetic-access-b", handleB)).toHaveLength(2);
    // One shared exchange per bearer; the refused replays never left.
    expect(exchanges("Bearer synthetic-access-a")).toHaveLength(1);
    expect(exchanges("Bearer synthetic-access-b")).toHaveLength(1);
    for (const request of sent.filter((r) => r.url.startsWith("/tracer/"))) {
      expect(request.organization).toBe(organization);
      // Exact Origin is the browser's own header; the client never forges it.
      expect(request.origin).toBeNull();
    }
  });

  it("never replays an exchange under a refreshed bearer, then pairs anew", async () => {
    network({
      expired: {
        bearer: "Bearer synthetic-access-a",
        urls: /attachment-media-sessions$/,
      },
    });
    const [result] = await resolveAttachments(
      projectId,
      itemsFor(occurrenceId),
    );
    expect(result.gateway_path).toContain(`/${handleB}/`);
    // The interceptor's replay (bearer B, captured under A) was refused before
    // sending; only the fresh pair under B reached the network.
    expect(exchanges("Bearer synthetic-access-a")).toHaveLength(1);
    expect(exchanges("Bearer synthetic-access-b")).toHaveLength(1);
    expect(sent.map((r) => r.url)).toEqual([
      "/tracer/v1/attachment-media-sessions",
      "/accounts/token/refresh/",
      "/tracer/v1/attachment-media-sessions",
      `/tracer/v1/projects/${projectId}/attachments/resolve`,
    ]);
  });

  it("reads the handle from Axios' lowercase response headers", async () => {
    network({ expired: { bearer: "none", urls: /^$/ } });
    const [result] = await resolveAttachments(
      projectId,
      itemsFor(occurrenceId),
    );
    expect(result.gateway_path).toBe(
      `/tracer/v1/attachment-media/${handleA}/${occurrenceId}/g`,
    );
    expect(resolvesWith("Bearer synthetic-access-a", handleA)).toHaveLength(1);
  });
});
