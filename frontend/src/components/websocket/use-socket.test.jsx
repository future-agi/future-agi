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

it("backs off while the server cannot complete authentication", () => {
  vi.useFakeTimers();
  render(tree());
  // Each handshake is accepted and then closed with 1011 while the backend
  // cannot reach its database or cache; the credential was never refused.
  const failHandshake = () =>
    act(() => {
      sockets.at(-1).emit("open");
      sockets.at(-1).emit("close", { code: 1011 });
    });
  const waitFor = (ms) =>
    act(() => {
      vi.advanceTimersByTime(ms);
    });

  failHandshake();
  waitFor(5000);
  expect(sockets).toHaveLength(2);
  expect(new URL(sockets[1].url).searchParams.get("token")).toBe(
    "synthetic-token",
  );
  failHandshake();
  waitFor(9999);
  expect(sockets).toHaveLength(2);
  waitFor(1);
  expect(sockets).toHaveLength(3);
  failHandshake();
  waitFor(19999);
  expect(sockets).toHaveLength(3);
  waitFor(1);
  expect(sockets).toHaveLength(4);

  // Once authentication succeeds the backoff starts again from the beginning.
  act(() => {
    sockets[3].emit("open");
  });
  act(() => {
    sockets[3].emit("close", { code: 1000 });
  });
  waitFor(5000);
  expect(sockets).toHaveLength(5);
});

const failHandshake = () =>
  act(() => {
    sockets.at(-1).emit("open");
    sockets.at(-1).emit("close", { code: 1011 });
  });
const waitFor = (ms) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });
// Fail handshakes with 1011 until the retry budget is spent.
const spendTheRetryBudget = () => {
  for (let i = 0; i < 10; i += 1) {
    failHandshake();
    waitFor(30000);
  }
  // The first socket plus MAX_RECONNECT_ATTEMPTS (10) retries.
  expect(sockets).toHaveLength(11);
};

it("keeps retrying unavailable authentication slowly after the reconnect limit", () => {
  vi.useFakeTimers();
  render(tree());
  spendTheRetryBudget();

  // An outage longer than the whole backoff does not end the retries.
  for (let expected = 12; expected <= 16; expected += 1) {
    failHandshake();
    waitFor(59999);
    expect(sockets).toHaveLength(expected - 1);
    waitFor(1);
    expect(sockets).toHaveLength(expected);
  }
  expect(new URL(sockets.at(-1).url).searchParams.get("token")).toBe(
    "synthetic-token",
  );

  // Once the server authenticates the socket, a later drop backs off from
  // the start again.
  act(() => {
    sockets.at(-1).emit("open");
    sockets.at(-1).emit("message", { data: JSON.stringify({ type: "pong" }) });
  });
  act(() => {
    sockets.at(-1).emit("close", { code: 1000 });
  });
  waitFor(5000);
  expect(sockets).toHaveLength(17);
});

it.each([4001, 4003])(
  "stops the slow retries when the server refuses the credential with %i",
  (code) => {
    vi.useFakeTimers();
    render(tree());
    spendTheRetryBudget();
    failHandshake();
    waitFor(60000);
    expect(sockets).toHaveLength(12);
    act(() => {
      sockets.at(-1).emit("open");
      sockets.at(-1).emit("close", { code });
    });
    waitLongerThanEveryBackoff();
    expect(sockets).toHaveLength(12);
  },
);

it("returns to the retry limit when a later failure is not 1011", () => {
  vi.useFakeTimers();
  render(tree());
  spendTheRetryBudget();
  failHandshake();
  waitFor(60000);
  expect(sockets).toHaveLength(12);
  // The server is now unreachable: the handshake fails without a 1011.
  act(() => {
    sockets.at(-1).emit("error");
    sockets.at(-1).emit("close", { code: 1006 });
  });
  waitLongerThanEveryBackoff();
  expect(sockets).toHaveLength(12);
});

it("still stops at the reconnect limit when authentication is not the cause", () => {
  vi.useFakeTimers();
  render(tree());
  act(() => {
    sockets[0].emit("open");
    sockets[0].emit("message", { data: JSON.stringify({ type: "pong" }) });
  });
  for (let i = 0; i < 20; i += 1) {
    act(() => {
      sockets.at(-1).emit("error");
      sockets.at(-1).emit("close", { code: 1006 });
    });
    waitFor(30000);
  }
  // The first socket plus MAX_RECONNECT_ATTEMPTS (10) retries.
  expect(sockets).toHaveLength(11);
  waitLongerThanEveryBackoff();
  expect(sockets).toHaveLength(11);
});

it("does not retry a credential the server refused after an outage", () => {
  vi.useFakeTimers();
  render(tree());
  act(() => {
    sockets[0].emit("open");
    sockets[0].emit("close", { code: 1011 });
  });
  act(() => {
    vi.advanceTimersByTime(5000);
  });
  expect(sockets).toHaveLength(2);
  act(() => {
    sockets[1].emit("open");
    sockets[1].emit("close", { code: 4003 });
  });
  waitLongerThanEveryBackoff();
  expect(sockets).toHaveLength(2);
});

it("starts a new backoff when an authenticated socket later closes with 1011", () => {
  vi.useFakeTimers();
  render(tree());
  for (let i = 0; i < 3; i += 1) {
    act(() => {
      sockets.at(-1).emit("open");
      sockets.at(-1).emit("close", { code: 1011 });
    });
    act(() => {
      vi.advanceTimersByTime(30000);
    });
  }
  expect(sockets).toHaveLength(4);

  // This socket authenticates and receives frames, then the server closes it.
  act(() => {
    sockets[3].emit("open");
    sockets[3].emit("message", { data: JSON.stringify({ type: "pong" }) });
  });
  act(() => {
    sockets[3].emit("close", { code: 1011 });
  });
  act(() => {
    vi.advanceTimersByTime(5000);
  });
  expect(sockets).toHaveLength(5);
});

it("gives a replaced credential the full retry budget during an outage", () => {
  vi.useFakeTimers();
  const { rerender } = render(tree());
  spendTheRetryBudget();
  // The spent budget leaves a slow retry scheduled.
  failHandshake();
  expect(vi.getTimerCount()).toBe(1);

  act(() => {
    state.user = { ...state.user, accessToken: "synthetic-token-2" };
    rerender(tree());
  });
  expect(sockets).toHaveLength(12);
  failHandshake();
  waitFor(5000);
  expect(sockets).toHaveLength(13);
  failHandshake();
  waitFor(10000);
  expect(sockets).toHaveLength(14);
  // The old credential's slow retry does not replace the new socket.
  waitLongerThanEveryBackoff();
  expect(sockets).toHaveLength(14);
  expect(sockets[13].close).not.toHaveBeenCalled();
  expect(
    sockets.slice(11).map((s) => new URL(s.url).searchParams.get("token")),
  ).toEqual(Array(3).fill("synthetic-token-2"));
});
