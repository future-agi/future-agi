import React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ user: null, navigate: vi.fn() }));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ authenticated: true, user: state.user }),
}));
vi.mock("react-router", () => ({ useNavigate: () => state.navigate }));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8100" }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn(), addBreadcrumb: vi.fn() },
}));

import { WebSocketProvider } from "./use-socket";

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
    this.close = vi.fn(() => { this.readyState = Socket.CLOSED; });
    sockets.push(this);
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  removeEventListener(type) { delete this.listeners[type]; }
}

beforeEach(() => {
  sockets.length = 0;
  state.user = { accessToken: "synthetic-token", organization: { id: "00000000-0000-4000-8000-000000000001" } };
  vi.stubGlobal("WebSocket", Socket);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("binds the socket to user-info's selected organization and reconnects on change", () => {
  const { rerender } = render(<WebSocketProvider><div /></WebSocketProvider>);
  expect(sockets).toHaveLength(1);
  expect(new URL(sockets[0].url).searchParams.get("organization_id")).toBe(state.user.organization.id);
  const old = sockets[0];
  act(() => {
    state.user = { ...state.user, organization: { id: "00000000-0000-4000-8000-000000000002" } };
    rerender(<WebSocketProvider><div /></WebSocketProvider>);
  });
  expect(old.close).toHaveBeenCalled();
  expect(sockets).toHaveLength(2);
  expect(new URL(sockets[1].url).searchParams.get("organization_id")).toBe(state.user.organization.id);
});
