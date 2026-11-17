import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { refresh, success } from "src/components/websocket/socket-recovery-fixture";
import { usePromptStreamUrl } from "./usePromptStreamUrl";

it("cancels a pending prompt ticket when its session is refreshed", async () => {
  let resolve;
  fetch.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const { result } = renderHook(() => usePromptStreamUrl());
  const oldResult = result.current().catch((error) => error);
  await refresh();
  await act(async () => resolve(success()));
  expect((await oldResult).name).toBe("AbortError");
  await result.current();
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("continues minting independent tickets for successful parallel requests", async () => {
  const { result } = renderHook(() => usePromptStreamUrl());
  await Promise.all([result.current(), result.current(), result.current()]);
  expect(fetch).toHaveBeenCalledTimes(3);
});
