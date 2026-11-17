import { expect, it } from "vitest";
import { fixture, flush, refresh } from "src/components/websocket/socket-recovery-fixture";
import { prewarmSocket } from "./clusterAnalyzeSocket";

it("stops repeated prewarms after refusal and uses the next published session", async () => {
  fetch.mockResolvedValue({ ok: false, status: 401 });
  const token = fixture.auth.user.accessToken;
  prewarmSocket({ token, workspaceId: "workspace" });
  await flush();
  for (let i = 0; i < 20; i += 1) {
    prewarmSocket({ token, workspaceId: "workspace" });
    await flush();
  }
  expect(fetch).toHaveBeenCalledTimes(1);
  const next = await refresh();
  prewarmSocket({ token, workspaceId: "workspace" });
  await flush();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[1][1].headers.Authorization).toBe(`Bearer ${next}`);
});
