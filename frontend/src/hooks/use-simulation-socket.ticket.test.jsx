import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { advance, flush, refresh, sockets, success } from "src/components/websocket/socket-recovery-fixture";
import { useSimulationSocket } from "./use-simulation-socket";

it("retires a pending old-session ticket on refresh", async () => {
  let resolve;
  fetch.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  renderHook(() => useSimulationSocket("test", () => {}));
  const token = await refresh();
  await flush();
  expect(fetch.mock.calls[1][1].headers.Authorization).toBe(`Bearer ${token}`);
  expect(sockets).toHaveLength(1);
  await act(async () => resolve(success()));
  expect(sockets).toHaveLength(1);
});

it("bounds socket-close retries before any successful open", async () => {
  renderHook(() => useSimulationSocket("test", () => {}));
  await flush();
  for (let i = 0; i < 20; i += 1) {
    const socket = sockets.at(-1);
    socket.readyState = WebSocket.CLOSED;
    await act(async () => socket.onclose({ code: 1006 }));
    await advance(30000);
  }
  expect(fetch.mock.calls.length).toBeLessThanOrEqual(10);
});
