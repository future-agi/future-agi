import React from "react";
import { act, cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  auth: null,
  sequence: 0,
  navigate: vi.fn(),
  responses: new Map(),
  rejections: new Map(),
  defaults: { headers: { common: {} } },
  log: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), addBreadcrumb: vi.fn() },
  store: {
    resetGeneration: 0,
    appendTextDelta: vi.fn(), addToolCall: vi.fn(), updateToolCall: vi.fn(),
    updateMessage: vi.fn(), setStreaming: vi.fn(), setActiveSkill: vi.fn(),
  },
}));

vi.mock("src/auth/hooks", () => ({ useAuthContext: () => state.auth }));
vi.mock("src/auth/hooks/use-auth-context", () => ({ useSafeAuthContext: () => state.auth }));
vi.mock("react-router", () => ({ useNavigate: () => state.navigate }));
vi.mock("src/config-global", () => ({ HOST_API: "https://example.invalid/__api" }));
vi.mock("src/utils/logger", () => ({ default: state.log }));
vi.mock("src/utils/axios", () => ({ default: {
  defaults: state.defaults,
  interceptors: { response: {
    use: (callback, rejection) => {
      const id = ++state.sequence;
      state.responses.set(id, callback);
      state.rejections.set(id, rejection);
      return id;
    },
    eject: (id) => { state.responses.delete(id); state.rejections.delete(id); },
  } },
} }));
vi.mock("src/contexts/WorkspaceContext", () => ({ useWorkspace: () => ({ currentWorkspaceId: "workspace" }) }));
vi.mock("src/utils/chunkRecovery", () => ({ importWithRecovery: vi.fn() }));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({ default: {
  getState: () => state.store, subscribe: () => () => {},
} }));
vi.mock("src/sections/falcon-ai/store/falconRequestGuard", () => ({ captureFalconRequest: () => () => true }));
vi.mock("src/sections/falcon-ai/store/falconSubmission", () => ({
  beginFalconSubmission: vi.fn(), bindFalconSubmission: vi.fn(), clearFalconSubmission: vi.fn(),
}));

import { requestWebSocketUrl, WebSocketProvider } from "./use-socket";
import { useSimulationSocket } from "src/hooks/use-simulation-socket";
import { useFalconSocket } from "src/sections/falcon-ai/hooks/useFalconSocket";
import { usePromptStreamUrl } from "src/sections/workbench/createPrompt/hooks/usePromptStreamUrl";

const sockets = [];
class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = FakeSocket.CONNECTING;
  listeners = new Map();
  constructor(url) { this.url = url; sockets.push(this); }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  removeEventListener(name, handler) { if (this.listeners.get(name) === handler) this.listeners.delete(name); }
  close() { this.readyState = FakeSocket.CLOSED; }
  send() {}
}

const flush = () => act(async () => { await Promise.resolve(); });
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const refuse = (status = 401) => ({ ok: false, status, json: vi.fn() });
const success = () => ({ ok: true, status: 200, json: async () => ({ ticket: "a".repeat(43) }) });
const mountProvider = () => render(<WebSocketProvider><span>Still signed in</span></WebSocketProvider>);
const clients = [
  ["notifications", mountProvider],
  ["simulation", () => renderHook(() => useSimulationSocket("test", () => {}))],
  ["assistant", () => renderHook(() => useFalconSocket())],
];

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  sockets.length = 0;
  state.responses.clear();
  state.rejections.clear();
  const accessToken = `synthetic-session-${++state.sequence}`;
  state.auth = { authenticated: true, user: { accessToken } };
  state.defaults.headers.common.Authorization = `Bearer ${accessToken}`;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(refuse()));
  vi.stubGlobal("WebSocket", FakeSocket);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe.each(clients)("%s ticket recovery", (_name, mount) => {
  it("stops refused tickets, reports once and stays signed in", async () => {
    const client = mount();
    await flush();
    await advance(20 * 60 * 1000);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(state.log.warn).toHaveBeenCalledTimes(1);
    expect(state.log.error).not.toHaveBeenCalled();
    expect(state.navigate).not.toHaveBeenCalled();
    expect(state.auth.authenticated).toBe(true);
    client.unmount();
    mount();
    await flush();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(state.log.warn).toHaveBeenCalledTimes(1);
  });

  it("retries with a changed context session", async () => {
    const client = mount();
    await flush();
    await advance(6 * 60 * 1000);
    fetch.mockClear();
    const accessToken = `replacement-${++state.sequence}`;
    state.auth = { authenticated: true, user: { accessToken } };
    state.defaults.headers.common.Authorization = `Bearer ${accessToken}`;
    if (_name === "notifications") client.rerender(<WebSocketProvider><span>Still signed in</span></WebSocketProvider>);
    else client.rerender();
    await flush();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${accessToken}`);
  });

  it("resumes only after a long hidden interval", async () => {
    mount();
    await flush();
    await advance(6 * 60 * 1000);
    const before = fetch.mock.calls.length;
    const visibility = async (value) => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value });
      await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    };
    await visibility("hidden");
    await advance(1000);
    await visibility("visible");
    expect(fetch).toHaveBeenCalledTimes(before);
    await visibility("hidden");
    await advance(5 * 60 * 1000);
    await visibility("visible");
    expect(fetch).toHaveBeenCalledTimes(before + 1);
    await advance(10 * 60 * 1000);
    expect(fetch).toHaveBeenCalledTimes(before + 1);
  });

  it("uses a silently refreshed session without changing the auth context", async () => {
    mount();
    await flush();
    const original = state.auth.user.accessToken;
    fetch.mockClear();
    const accessToken = `refreshed-${++state.sequence}`;
    state.defaults.headers.common.Authorization = `Bearer ${accessToken}`;
    await act(async () => {
      for (const callback of [...state.responses.values()]) callback({ status: 200 });
    });
    expect(state.auth.user.accessToken).toBe(original);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${accessToken}`);
  });

  it("bounds transient ticket failures too", async () => {
    fetch.mockResolvedValue(refuse(503));
    const client = mount();
    await flush();
    await advance(30 * 60 * 1000);
    expect(fetch.mock.calls.length).toBeGreaterThan(1);
    expect(fetch.mock.calls.length).toBeLessThanOrEqual(10);
    expect(state.log.warn).toHaveBeenCalledTimes(1);
    client.unmount();
    const attempts = fetch.mock.calls.length;
    await advance(60 * 1000);
    expect(fetch).toHaveBeenCalledTimes(attempts);
  });
});

it("shares refusal state across channels without sharing successful tickets", async () => {
  const token = state.auth.user.accessToken;
  await expect(requestWebSocketUrl(token, "/ws/connect/")).rejects.toThrow("Socket ticket refused");
  await expect(requestWebSocketUrl(token, "/ws/prompt-stream/")).rejects.toThrow("Socket ticket refused");
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(state.log.warn).toHaveBeenCalledTimes(1);
  fetch.mockClear().mockResolvedValue(success());
  const next = `parallel-${++state.sequence}`;
  await Promise.all([
    requestWebSocketUrl(next, "/ws/prompt-stream/"),
    requestWebSocketUrl(next, "/ws/prompt-stream/"),
  ]);
  expect(fetch).toHaveBeenCalledTimes(2);
  for (const [url, options] of fetch.mock.calls) {
    expect(url).toBe("https://example.invalid/__api/accounts/handoff/ws-ticket/");
    expect(options).toMatchObject({ credentials: "omit", redirect: "error", cache: "no-store" });
    expect(options.headers.Authorization).toBe(`Bearer ${next}`);
    expect(options.body).not.toContain(next);
    expect(url).not.toContain(next);
  }
});

it("never includes response bodies or credentials in diagnostics", async () => {
  const response = refuse();
  response.json.mockResolvedValue({ detail: "private upstream content" });
  fetch.mockResolvedValue(response);
  await expect(requestWebSocketUrl(state.auth.user.accessToken, "/ws/connect/")).rejects.toThrow();
  expect(state.log.warn).toHaveBeenCalledTimes(1);
  expect(JSON.stringify(state.log.warn.mock.calls)).not.toContain(state.auth.user.accessToken);
  expect(JSON.stringify(state.log.warn.mock.calls)).not.toContain("private upstream content");
  expect(response.json).not.toHaveBeenCalled();
});

it("updates the lazy prompt factory after silent refresh", async () => {
  const { result } = renderHook(() => usePromptStreamUrl());
  await expect(result.current()).rejects.toThrow();
  fetch.mockClear().mockResolvedValue(success());
  const accessToken = `prompt-refresh-${++state.sequence}`;
  state.defaults.headers.common.Authorization = `Bearer ${accessToken}`;
  await act(async () => {
    for (const callback of [...state.responses.values()]) callback({ status: 200 });
  });
  await result.current();
  expect(fetch.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${accessToken}`);
});

it("observes refresh even if the retried HTTP operation fails, without altering that failure", async () => {
  mountProvider();
  await flush();
  fetch.mockClear();
  const token = `refresh-after-refusal-${++state.sequence}`;
  state.defaults.headers.common.Authorization = `Bearer ${token}`;
  const failure = new Error("Operation refused");
  await act(async () => {
    for (const reject of [...state.rejections.values()]) {
      await expect(reject(failure)).rejects.toBe(failure);
    }
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${token}`);
});

it("never revives a signed-out session from retained HTTP defaults", async () => {
  state.auth = { authenticated: false, user: null };
  mountProvider();
  await act(async () => {
    for (const callback of [...state.responses.values()]) callback({ status: 200 });
  });
  await advance(10 * 60 * 1000);
  expect(fetch).not.toHaveBeenCalled();
});

it("does not create a socket from a ticket that arrives after unmount", async () => {
  let resolve;
  fetch.mockImplementation(() => new Promise((done) => { resolve = done; }));
  const client = mountProvider();
  client.unmount();
  await act(async () => resolve(success()));
  expect(sockets).toHaveLength(0);
  expect(state.responses.size).toBe(0);
});
