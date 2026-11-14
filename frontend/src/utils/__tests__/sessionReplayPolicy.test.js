import { describe, expect, it } from "vitest";

import {
  isSessionReplayBlockedPath,
  SESSION_REPLAY_BLOCKED_PATH_PREFIXES,
  SESSION_REPLAY_URL_BLOCKLIST,
} from "../sessionReplayPolicy";

describe("sessionReplayPolicy", () => {
  it.each([
    "/dashboard/observe",
    "/dashboard/observe/project-id/llm-tracing",
    "/dashboard/users/guest-1",
    "/dashboard/dashboards/dashboard-id",
    "/dashboard/tasks/create",
    "/dashboard/annotations/queues/queue-id",
    "/dashboard/error-feed",
  ])("blocks mutation-heavy route %s", (pathname) => {
    expect(isSessionReplayBlockedPath(pathname)).toBe(true);
  });

  it.each([
    "/dashboard/develop",
    "/dashboard/prompt",
    "/dashboard/user-settings",
    "/auth/jwt/login",
  ])("does not overmatch route %s", (pathname) => {
    expect(isSessionReplayBlockedPath(pathname)).toBe(false);
  });

  it("produces a PostHog regex block for every configured prefix", () => {
    expect(SESSION_REPLAY_URL_BLOCKLIST).toHaveLength(
      SESSION_REPLAY_BLOCKED_PATH_PREFIXES.length,
    );

    for (const { url, matching } of SESSION_REPLAY_URL_BLOCKLIST) {
      expect(matching).toBe("regex");
      const expression = new RegExp(url);
      expect(
        SESSION_REPLAY_BLOCKED_PATH_PREFIXES.some((prefix) =>
          expression.test(`https://app.futureagi.com${prefix}`),
        ),
      ).toBe(true);
    }
  });
});
