import { act, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

const fixture = vi.hoisted(() => ({
  auth: null,
  count: 0,
  responses: new Map(),
  listeners: new Set(),
  defaults: { headers: { common: {} } },
  store: {
    resetGeneration: 0,
    appendTextDelta: vi.fn(), addToolCall: vi.fn(), updateToolCall: vi.fn(),
    updateMessage: vi.fn(), setStreaming: vi.fn(), setActiveSkill: vi.fn(),
  },
}));

vi.mock("src/auth/hooks", () => ({ useAuthContext: () => fixture.auth }));
vi.mock("src/config-global", () => ({ HOST_API: "https://example.invalid" }));
vi.mock("src/utils/logger", () => ({ default: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), addBreadcrumb: vi.fn() } }));
vi.mock("src/utils/axios", () => ({ default: {
  defaults: fixture.defaults,
  interceptors: { response: {
    use: (callback) => { const id = ++fixture.count; fixture.responses.set(id, callback); return id; },
    eject: (id) => fixture.responses.delete(id),
  } },
} }));
vi.mock("src/contexts/WorkspaceContext", () => ({ useWorkspace: () => ({ currentWorkspaceId: "workspace" }) }));
vi.mock("src/utils/chunkRecovery", () => ({ importWithRecovery: vi.fn() }));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({ default: {
  getState: () => fixture.store,
  subscribe: (listener) => { fixture.listeners.add(listener); return () => fixture.listeners.delete(listener); },
} }));
vi.mock("src/sections/falcon-ai/store/falconRequestGuard", () => ({ captureFalconRequest: () => () => true }));
vi.mock("src/sections/falcon-ai/store/falconSubmission", () => ({
  beginFalconSubmission: vi.fn(), bindFalconSubmission: vi.fn(), clearFalconSubmission: vi.fn(),
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconAPI", () => ({ createConversation: vi.fn() }));
vi.mock("src/pages/dashboard/error-feed/store", () => ({ useErrorFeedStore: { setState: vi.fn() } }));

export { fixture };
export const sockets = [];
class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = FakeSocket.CONNECTING;
  constructor(url) { this.url = url; sockets.push(this); }
  close() { this.readyState = FakeSocket.CLOSED; }
  send() {}
}
export const success = () => ({ ok: true, status: 200, json: async () => ({ ticket: "b".repeat(43) }) });
export const flush = () => act(async () => { await Promise.resolve(); });
export const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
export const refresh = async () => {
  const token = `synthetic-refreshed-${++fixture.count}`;
  fixture.defaults.headers.common.Authorization = `Bearer ${token}`;
  await act(async () => {
    for (const callback of [...fixture.responses.values()]) callback({ status: 200 });
  });
  return token;
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  sockets.length = 0;
  fixture.responses.clear();
  const accessToken = `synthetic-session-${++fixture.count}`;
  fixture.auth = { authenticated: true, user: { accessToken } };
  fixture.defaults.headers.common.Authorization = `Bearer ${accessToken}`;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success()));
  vi.stubGlobal("WebSocket", FakeSocket);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
