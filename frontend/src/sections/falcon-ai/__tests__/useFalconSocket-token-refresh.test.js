import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  auth: { user: null },
  get: vi.fn(),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => mocks.auth,
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws-1" }),
}));

vi.mock("src/config-global", () => ({
  HOST_API: "https://api.example.test",
  CURRENT_ENVIRONMENT: "local",
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

import { useFalconSocket } from "../hooks/useFalconSocket";
import useFalconStore from "../store/useFalconStore";

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.onclose = null;
    this.sent = [];
    this.createdAt = Date.now();
    FakeWebSocket.instances.push(this);
  }

  send(data) {
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  closeWith(code) {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code });
  }

  // The server accepts the handshake, then rejects the token.
  acceptThenReject() {
    this.open();
    this.closeWith(4003);
  }

  get token() {
    return new URL(this.url).searchParams.get("token");
  }
}

const sockets = () => FakeWebSocket.instances;
const lastSocket = () => sockets()[sockets().length - 1];

const signIn = (contextToken) => {
  mocks.auth = { user: { id: "u1", accessToken: contextToken } };
};

// Each refresh stores a new token, like the REST client's 401 interceptor.
const refreshStoresNewTokens = () => {
  let n = 1;
  mocks.get.mockImplementation(async () => {
    n += 1;
    localStorage.setItem("accessToken", `token-${n}`);
    return { data: {} };
  });
};

describe("useFalconSocket token handling", () => {
  let originalWebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    originalWebSocket = globalThis.WebSocket;
    globalThis.WebSocket = FakeWebSocket;
    FakeWebSocket.instances = [];
    mocks.get.mockReset();
    useFalconStore.getState().resetAll();
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
    localStorage.setItem("accessToken", "refreshed-token");

    renderHook(() => useFalconSocket());

    expect(sockets()).toHaveLength(1);
    expect(lastSocket().url).toContain("/ws/falcon-ai/");
    expect(lastSocket().token).toBe("refreshed-token");
  });

  it("refreshes the token before reconnecting after a 4003 rejection", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");
    refreshStoresNewTokens();

    renderHook(() => useFalconSocket());
    expect(lastSocket().token).toBe("token-1");

    await act(async () => {
      lastSocket().acceptThenReject();
    });
    expect(mocks.get).toHaveBeenCalledWith("/accounts/user-info/");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });

    expect(sockets()).toHaveLength(2);
    expect(lastSocket().token).toBe("token-2");
  });

  it("backs off between repeated 4003 rejections and then stops reconnecting", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");
    refreshStoresNewTokens();

    renderHook(() => useFalconSocket());

    // Reject every socket the hook opens over a long window.
    for (let i = 0; i < 60; i += 1) {
      const socket = lastSocket();
      if (socket.readyState === FakeWebSocket.CONNECTING) {
        await act(async () => {
          socket.acceptThenReject();
        });
      }
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
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

    renderHook(() => useFalconSocket());
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
    refreshStoresNewTokens();

    renderHook(() => useFalconSocket());
    for (let i = 0; i < 2; i += 1) {
      await act(async () => {
        lastSocket().acceptThenReject();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60000);
      });
    }

    // This socket is accepted for real: the server answers a frame.
    const good = lastSocket();
    await act(async () => {
      good.open();
      good.onmessage({ data: JSON.stringify({ type: "pong" }) });
    });
    const before = sockets().length;

    // A later rejection gets the full budget again.
    await act(async () => {
      good.closeWith(4003);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });

    expect(sockets().length).toBe(before + 1);
  });

  it("still reconnects after an ordinary close, without a token refresh", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");

    renderHook(() => useFalconSocket());
    await act(async () => {
      lastSocket().open();
      lastSocket().closeWith(1006);
    });
    // The REST client refreshed the token while the socket was down.
    localStorage.setItem("accessToken", "token-2");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(mocks.get).not.toHaveBeenCalled();
    expect(sockets()).toHaveLength(2);
    expect(lastSocket().token).toBe("token-2");
  });

  it("still does not retry on the other auth close codes", async () => {
    signIn("token-1");
    localStorage.setItem("accessToken", "token-1");

    renderHook(() => useFalconSocket());
    await act(async () => {
      lastSocket().open();
      lastSocket().closeWith(4001);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120000);
    });

    expect(mocks.get).not.toHaveBeenCalled();
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

    const view = renderHook(() => useFalconSocket());
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
});
