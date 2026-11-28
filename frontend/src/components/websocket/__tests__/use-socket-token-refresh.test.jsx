import React from "react";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  auth: { user: null, authenticated: false },
  get: vi.fn(),
}));

vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => mocks.auth,
}));

// Under BrowserRouter, useNavigate returns a new function after every
// navigation; model that with a new function per render.
vi.mock("react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("src/config-global", () => ({
  HOST_API: "https://api.example.test",
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: { auth: { me: "/accounts/user-info/" } },
}));

vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn(),
}));

vi.mock("src/utils/logger", () => ({
  default: {
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    addBreadcrumb: vi.fn(),
  },
}));

import logger from "src/utils/logger";
import { WebSocketProvider } from "../use-socket";

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances = [];

  constructor(url, protocols) {
    this.url = url;
    this.protocols = protocols;
    this.readyState = FakeWebSocket.CONNECTING;
    this.listeners = {};
    this.sent = [];
    this.createdAt = Date.now();
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type, handler) {
    (this.listeners[type] ||= new Set()).add(handler);
  }

  removeEventListener(type, handler) {
    this.listeners[type]?.delete(handler);
  }

  send(data) {
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }

  emit(type, event = {}) {
    [...(this.listeners[type] || [])].forEach((handler) => handler(event));
  }

  // The server accepts the handshake, then rejects the token.
  acceptThenReject() {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open");
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close", { code: 4003 });
  }
}

const sockets = () => FakeWebSocket.instances;
const lastSocket = () => sockets()[sockets().length - 1];

const signIn = (contextToken) => {
  mocks.auth = {
    authenticated: true,
    user: { id: "u1", accessToken: contextToken },
  };
};

const renderProvider = () =>
  render(
    <WebSocketProvider>
      <div />
    </WebSocketProvider>,
  );

describe("WebSocketProvider token handling", () => {
  let originalWebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    originalWebSocket = globalThis.WebSocket;
    globalThis.WebSocket = FakeWebSocket;
    FakeWebSocket.instances = [];
    mocks.get.mockReset();
    localStorage.clear();
    localStorage.setItem("rememberMe", "true");
    localStorage.setItem("refreshToken", "refresh-1");
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
    localStorage.clear();
  });

  it("connects with the stored token the REST client refreshed, not the stale context token", () => {
    signIn("stale-context-token");
    localStorage.setItem("accessToken", "refreshed-token==");

    renderProvider();

    expect(sockets()).toHaveLength(1);
    expect(lastSocket().url).toBe("wss://api.example.test/ws/connect/");
    expect(lastSocket().protocols).toEqual([
      "fi.auth.v1",
      "bearer.refreshed-token%3D%3D",
    ]);
    act(() => lastSocket().emit("error"));
    expect(logger.addBreadcrumb).toHaveBeenLastCalledWith(
      expect.objectContaining({
        data: {
          path: "/ws/connect/",
          readyState: "CONNECTING",
          reconnectAttempts: 0,
        },
      }),
    );
  });

  it("refreshes the token before reconnecting after a 4003 rejection", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");
    mocks.get.mockImplementation(async () => {
      // The REST interceptor refreshes on 401 and stores the new token.
      localStorage.setItem("accessToken", "token-2");
      return { data: {} };
    });

    renderProvider();
    expect(lastSocket().protocols).toEqual(["fi.auth.v1", "bearer.token-1"]);

    await act(async () => {
      lastSocket().acceptThenReject();
    });
    expect(mocks.get).toHaveBeenCalledWith("/accounts/user-info/");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });

    expect(sockets()).toHaveLength(2);
    expect(lastSocket().protocols).toEqual(["fi.auth.v1", "bearer.token-2"]);
  });

  it("backs off between repeated 4003 rejections and then stops reconnecting", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");
    let n = 1;
    mocks.get.mockImplementation(async () => {
      n += 1;
      localStorage.setItem("accessToken", `token-${n}`);
      return { data: {} };
    });

    renderProvider();

    // Reject every socket the provider opens over a long window.
    for (let i = 0; i < 40; i += 1) {
      const socket = lastSocket();
      if (socket.readyState === FakeWebSocket.CONNECTING) {
        await act(async () => {
          socket.acceptThenReject();
        });
      }
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
    }

    // Three attempts, then it gives up instead of reconnecting forever.
    expect(sockets()).toHaveLength(3);
    const gaps = sockets()
      .slice(1)
      .map((socket, i) => socket.createdAt - sockets()[i].createdAt);
    expect(gaps[1]).toBeGreaterThan(gaps[0]);
  });

  it("stops after a rejection when there is no refresh token to use", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");
    localStorage.removeItem("refreshToken");

    renderProvider();
    await act(async () => {
      lastSocket().acceptThenReject();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120000);
    });

    expect(mocks.get).not.toHaveBeenCalled();
    expect(sockets()).toHaveLength(1);
  });

  it("resets the rejection budget once the server answers", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");
    let n = 1;
    mocks.get.mockImplementation(async () => {
      n += 1;
      localStorage.setItem("accessToken", `token-${n}`);
      return { data: {} };
    });

    renderProvider();
    await act(async () => {
      lastSocket().acceptThenReject();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    await act(async () => {
      lastSocket().acceptThenReject();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });

    // This socket is accepted for real: the server answers a frame.
    const good = lastSocket();
    await act(async () => {
      good.readyState = FakeWebSocket.OPEN;
      good.emit("open");
      good.emit("message", { data: JSON.stringify({ type: "pong" }) });
    });
    const before = sockets().length;

    // Later rejections get the full budget again.
    await act(async () => {
      good.readyState = FakeWebSocket.CLOSED;
      good.emit("close", { code: 4003 });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });

    expect(sockets().length).toBe(before + 1);
  });

  it("still reconnects after an ordinary close", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");

    renderProvider();
    const socket = lastSocket();
    await act(async () => {
      socket.readyState = FakeWebSocket.OPEN;
      socket.emit("open");
      socket.readyState = FakeWebSocket.CLOSED;
      socket.emit("close", { code: 1000 });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(mocks.get).not.toHaveBeenCalled();
    expect(sockets()).toHaveLength(2);
  });

  it("does not skip the reconnect delay when the page re-renders after a navigation", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");

    const view = renderProvider();
    const socket = lastSocket();
    await act(async () => {
      socket.readyState = FakeWebSocket.OPEN;
      socket.emit("open");
      socket.readyState = FakeWebSocket.CLOSED;
      socket.emit("close", { code: 1000 });
    });

    view.rerender(
      <WebSocketProvider>
        <div />
      </WebSocketProvider>,
    );

    expect(sockets()).toHaveLength(1);
  });

  it("does not open a socket when unmounted while the token refresh is in flight", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");
    let finishRefresh;
    mocks.get.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishRefresh = () => {
            localStorage.setItem("accessToken", "token-2");
            resolve({ data: {} });
          };
        }),
    );

    const view = renderProvider();
    await act(async () => {
      lastSocket().acceptThenReject();
    });
    expect(mocks.get).toHaveBeenCalledTimes(1);

    view.unmount();
    await act(async () => {
      finishRefresh();
      await vi.advanceTimersByTimeAsync(60000);
    });

    expect(sockets()).toHaveLength(1);
  });

  it("does not let a stale token refresh reconnect after a newer socket closed", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");
    let finishRefresh;
    mocks.get.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishRefresh = () => {
            localStorage.setItem("accessToken", "token-2");
            resolve({ data: {} });
          };
        }),
    );

    renderProvider();
    const first = lastSocket();
    // An error schedules a retry, then the server rejects the token, so a
    // refresh is in flight while that retry is still pending.
    await act(async () => {
      first.readyState = FakeWebSocket.CLOSED;
      first.emit("error");
      first.emit("close", { code: 4003 });
    });
    expect(mocks.get).toHaveBeenCalledTimes(1);

    // The pending retry opens a replacement socket, which closes with 1006
    // (no reconnect).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(sockets()).toHaveLength(2);
    await act(async () => {
      lastSocket().readyState = FakeWebSocket.CLOSED;
      lastSocket().emit("close", { code: 1006 });
    });

    // The first socket's refresh finishes afterwards and must not reconnect.
    await act(async () => {
      finishRefresh();
      await vi.advanceTimersByTimeAsync(120000);
    });

    expect(sockets()).toHaveLength(2);
  });
});
