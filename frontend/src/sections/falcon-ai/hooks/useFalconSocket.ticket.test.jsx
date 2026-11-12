import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { advance, fixture, flush, refresh, sockets, success } from "src/components/websocket/socket-recovery-fixture";
import { useFalconSocket } from "./useFalconSocket";

it("retires a pending old-session ticket on refresh", async () => {
  let resolve;
  fetch.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  renderHook(() => useFalconSocket());
  const token = await refresh();
  await flush();
  expect(fetch.mock.calls[1][1].headers.Authorization).toBe(`Bearer ${token}`);
  expect(sockets).toHaveLength(1);
  await act(async () => resolve(success()));
  expect(sockets).toHaveLength(1);
});

it("does not revive a retired workspace on visibility recovery", async () => {
  renderHook(() => useFalconSocket());
  await flush();
  await act(async () => {
    for (const listener of fixture.listeners) listener({ resetGeneration: 1 }, { resetGeneration: 0 });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await advance(5 * 60 * 1000);
  await act(async () => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("does not leave retry work after unmount", async () => {
  fetch.mockResolvedValue({ ok: false, status: 503 });
  const client = renderHook(() => useFalconSocket());
  await flush();
  client.unmount();
  await advance(10 * 60 * 1000);
  expect(fetch).toHaveBeenCalledTimes(1);
});
