import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client, { endpoints } from "src/utils/axios";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import { useErrorFeedStore } from "../store";
import { startRun, runFollowUp } from "../clusterAnalyzeSocket";

vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/api/contracts/openapi-contract", () => ({
  assertContractedRequestConfig: (x) => x,
  assertContractedResponse: (x) => x,
  findOpenApiEndpoint: vi.fn(),
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: vi.fn(),
  getIsRefreshing: vi.fn(),
  getRefreshToken: vi.fn(),
  getRememberMe: vi.fn(),
  processQueue: vi.fn(),
  refreshTokenRequest: vi.fn(),
  setIsRefreshing: vi.fn(),
  setSession: vi.fn(),
}));
const originalAdapter = client.defaults.adapter;
const args = {
  clusterId: "cluster",
  projectId: "project",
  token: "offline",
  workspaceId: "old",
};
let sockets;
let finishCreate;
const drain = async () => {
  for (let i = 0; i < 30; i += 1) await Promise.resolve();
};
const sends = () =>
  sockets.flatMap((s) => s.send.mock.calls).map(([p]) => JSON.parse(p));
function recover() {
  useFalconStore.getState().resetAll();
  client.defaults.headers.common["X-Workspace-Id"] = "landing";
  // Model the page discarding its old thread state during scope recovery.
  useErrorFeedStore.setState({ analyzeThreadsByCluster: {} });
}
async function openSockets() {
  await drain();
  for (const socket of sockets) {
    socket.readyState = 1;
    socket.onopen?.();
  }
  await drain();
}
beforeEach(() => {
  vi.useFakeTimers();
  useFalconStore.getState().resetAll();
  useErrorFeedStore.setState({ analyzeThreadsByCluster: {} });
  client.defaults.headers.common["X-Workspace-Id"] = "old";
  client.defaults.headers.common["X-Organization-Id"] = "org";
  client.defaults.adapter = (c) =>
    new Promise((resolve) => {
      expect(c.url).toBe(endpoints.falconAI.conversations);
      finishCreate = () =>
        resolve({
          status: 200,
          headers: {},
          config: c,
          data: { id: "created" },
        });
    });
  sockets = [];
  finishCreate = undefined;
  vi.stubGlobal(
    "WebSocket",
    class {
      static OPEN = 1;
      static CONNECTING = 0;
      readyState = 0;
      send = vi.fn();
      constructor(url) {
        this.url = url;
        sockets.push(this);
      }
      close() {
        this.readyState = 3;
        this.onclose?.();
      }
    },
  );
});
afterEach(() => {
  useFalconStore.getState().resetAll();
  sockets.forEach((s) => s.close());
  client.defaults.adapter = originalAdapter;
  delete client.defaults.headers.common["X-Workspace-Id"];
  delete client.defaults.headers.common["X-Organization-Id"];
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
async function begin() {
  const run = startRun(args);
  await drain();
  expect(finishCreate).toBeTypeOf("function");
  return { run };
}
describe("error-feed Falcon completion scope", () => {
  it("keeps authorized creation and socket send functional", async () => {
    const { run } = await begin();
    finishCreate();
    await openSockets();
    await run;
    expect(sends()).toHaveLength(1);
    expect(sends()[0]).toMatchObject({
      conversation_id: "created",
      message: "/cluster-rca",
    });
  });
  it("does not create a socket or restore a thread after stale creation", async () => {
    const { run } = await begin();
    recover();
    finishCreate();
    await openSockets();
    await run;
    expect(sockets).toHaveLength(0);
    expect(sends()).toHaveLength(0);
    expect(useErrorFeedStore.getState().analyzeThreadsByCluster).toEqual({});
  });
  it("does not send when socket readiness completes after recovery", async () => {
    const { run } = await begin();
    finishCreate();
    await drain();
    expect(sockets).toHaveLength(1);
    recover();
    await openSockets();
    await run;
    expect(sends()).toHaveLength(0);
    expect(useErrorFeedStore.getState().analyzeThreadsByCluster).toEqual({});
    await vi.advanceTimersByTimeAsync(31000);
    expect(sends()).toHaveLength(0);
  });
  it("cancels the delivery watchdog on recovery rather than leaving queued work", async () => {
    const { run } = await begin();
    finishCreate();
    await openSockets();
    await run;
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    recover();
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(31000);
    expect(sends()).toHaveLength(1);
  });
  it("ignores retired run frames and failure callbacks", async () => {
    const { run } = await begin();
    finishCreate();
    await openSockets();
    await run;
    const old = sockets[0];
    recover();
    old.onmessage({
      data: JSON.stringify({
        type: "rca_reasoning",
        data: { conversation_id: "created", reasoning: "Old private result" },
      }),
    });
    old.onclose();
    await vi.advanceTimersByTimeAsync(31000);
    expect(useErrorFeedStore.getState().analyzeThreadsByCluster).toEqual({});
    expect(sends()).toHaveLength(1);
  });
  it.each([false, true])(
    "follow-up socket completion respects recovery=%s",
    async (reset) => {
      useErrorFeedStore.getState().setAnalyzeThread("cluster", {
        conversationId: "previous",
        messages: [],
        runState: "done",
      });
      const run = runFollowUp({ ...args, question: "Follow up" });
      await drain();
      if (reset) recover();
      await openSockets();
      await run;
      expect(sends()).toHaveLength(reset ? 0 : 1);
      if (reset)
        expect(useErrorFeedStore.getState().analyzeThreadsByCluster).toEqual(
          {},
        );
    },
  );
});
