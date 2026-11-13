import React, { useContext } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ user: null, navigate: vi.fn() }));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ authenticated: true, user: state.user }),
}));
vi.mock("react-router", () => ({ useNavigate: () => state.navigate }));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8100" }));
vi.mock("src/utils/logger", () => ({
  default: {
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    addBreadcrumb: vi.fn(),
  },
}));

import logger from "src/utils/logger";
import { WebSocketContext, WebSocketProvider } from "./use-socket";

const sockets = [];
class Socket {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSING = 2;
  static CLOSED = 3;
  constructor(url) {
    this.url = url;
    this.readyState = Socket.OPEN;
    this.listeners = {};
    this.close = vi.fn(() => {
      this.readyState = Socket.CLOSED;
    });
    sockets.push(this);
  }
  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }
  removeEventListener(type) {
    delete this.listeners[type];
  }
  emit(type, event = {}) {
    if (type === "close" || type === "error") this.readyState = Socket.CLOSED;
    this.listeners[type]?.(event);
  }
}

beforeEach(() => {
  sockets.length = 0;
  state.user = {
    accessToken: "synthetic-token",
    organization: { id: "00000000-0000-4000-8000-000000000001" },
  };
  vi.stubGlobal("WebSocket", Socket);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.clearAllMocks();
});

const context = { current: null };
function CaptureContext() {
  context.current = useContext(WebSocketContext);
  return null;
}
const tree = () => (
  <WebSocketProvider>
    <CaptureContext />
  </WebSocketProvider>
);
const waitLongerThanEveryBackoff = () =>
  act(() => {
    vi.advanceTimersByTime(10 * 60 * 1000);
  });

it("binds the socket to user-info's selected organization and reconnects on change", () => {
  const { rerender } = render(
    <WebSocketProvider>
      <div />
    </WebSocketProvider>,
  );
  expect(sockets).toHaveLength(1);
  expect(new URL(sockets[0].url).searchParams.get("organization_id")).toBe(
    state.user.organization.id,
  );
  const old = sockets[0];
  act(() => {
    state.user = {
      ...state.user,
      organization: { id: "00000000-0000-4000-8000-000000000002" },
    };
    rerender(
      <WebSocketProvider>
        <div />
      </WebSocketProvider>,
    );
  });
  expect(old.close).toHaveBeenCalled();
  expect(sockets).toHaveLength(2);
  expect(new URL(sockets[1].url).searchParams.get("organization_id")).toBe(
    state.user.organization.id,
  );
});

it.each([4001, 4002, 4003])(
  "does not reconnect after the server closes with authorization code %i",
  (code) => {
    vi.useFakeTimers();
    render(tree());
    expect(sockets).toHaveLength(1);
    act(() => {
      sockets[0].emit("open");
      sockets[0].emit("close", { code });
    });
    waitLongerThanEveryBackoff();
    // Heartbeat, a component send and a network change must not retry either.
    act(() => {
      expect(context.current.sendMessage({ type: "subscribe" })).toBe(false);
      window.dispatchEvent(new Event("online"));
    });
    waitLongerThanEveryBackoff();
    expect(sockets).toHaveLength(1);
    expect(logger.error).not.toHaveBeenCalled();
  },
);

it("opens a fresh socket when the refused credential is replaced", () => {
  vi.useFakeTimers();
  const { rerender } = render(tree());
  act(() => {
    sockets[0].emit("open");
    sockets[0].emit("close", { code: 4002 });
  });
  waitLongerThanEveryBackoff();
  expect(sockets).toHaveLength(1);
  act(() => {
    state.user = { ...state.user, accessToken: "synthetic-token-2" };
    rerender(tree());
  });
  expect(sockets).toHaveLength(2);
  expect(new URL(sockets[1].url).searchParams.get("token")).toBe(
    "synthetic-token-2",
  );
});

it("does not open a socket for a session without an organization", () => {
  vi.useFakeTimers();
  state.user = {
    accessToken: "synthetic-token",
    organization: null,
    requires_org_setup: true,
  };
  render(tree());
  waitLongerThanEveryBackoff();
  expect(sockets).toHaveLength(0);
});

it("still reconnects after an abnormal drop of an open socket", () => {
  vi.useFakeTimers();
  render(tree());
  act(() => {
    sockets[0].emit("open");
    sockets[0].emit("error");
    sockets[0].emit("close", { code: 1006 });
  });
  act(() => {
    vi.advanceTimersByTime(5000);
  });
  expect(sockets).toHaveLength(2);
});

it("reconnects when the server could not complete authentication", () => {
  vi.useFakeTimers();
  render(tree());
  // The handshake is accepted and then closed with 1011 while the backend
  // cannot reach its database or cache; the credential was never refused.
  act(() => {
    sockets[0].emit("open");
    sockets[0].emit("close", { code: 1011 });
  });
  act(() => {
    vi.advanceTimersByTime(5000);
  });
  expect(sockets).toHaveLength(2);
  expect(new URL(sockets[1].url).searchParams.get("token")).toBe(
    "synthetic-token",
  );
  act(() => {
    sockets[1].emit("open");
    sockets[1].emit("close", { code: 1011 });
  });
  act(() => {
    vi.advanceTimersByTime(5000);
  });
  expect(sockets).toHaveLength(3);
  // Once the credential itself is refused, the same token is not retried.
  act(() => {
    sockets[2].emit("open");
    sockets[2].emit("close", { code: 4003 });
  });
  waitLongerThanEveryBackoff();
  expect(sockets).toHaveLength(3);
});
