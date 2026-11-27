import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(() => Promise.resolve({ data: {} })) },
}));

import axios from "src/utils/axios";
import {
  approveHarnessAdaptation,
  useHarnessAdaptationDesign,
} from "./releases";

const ID = "6f7f0a44-7f5b-4b6d-9d62-1ad9a4a5cf16";

beforeEach(() => {
  axios.get.mockReset();
  axios.post.mockClear();
});

describe("approveHarnessAdaptation", () => {
  it("sends the empty object its contract takes", async () => {
    await approveHarnessAdaptation(ID);
    const [url, data] = axios.post.mock.calls[0];
    expect(url).toBe(`/simulate/api/harness-adaptations/${ID}/approve/`);
    expect(
      validateContractedRequestConfig({ url, method: "post", data }),
    ).toMatchObject({ ok: true });
  });
});

describe("useHarnessAdaptationDesign", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const poll = async (ms) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () => useHarnessAdaptationDesign(ID),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    await act(() => vi.advanceTimersByTimeAsync(100));
    await act(() => vi.advanceTimersByTimeAsync(ms));
    return { result, unmount };
  };

  it.each([403, 404, 409])(
    "stops polling a design refused with %s before it was ever read",
    async (statusCode) => {
      axios.get.mockRejectedValue({ statusCode });
      const { result, unmount } = await poll(21000);
      expect(result.current.isError).toBe(true);
      expect(axios.get).toHaveBeenCalledTimes(1);
      unmount();
    },
  );

  it("keeps polling through a server error, and once it has been read", async () => {
    axios.get
      .mockRejectedValueOnce({ statusCode: 502 })
      .mockResolvedValueOnce({ data: { state: "checking" } })
      .mockRejectedValueOnce({ statusCode: 429 })
      .mockResolvedValue({ data: { state: "ready" } });
    const { result, unmount } = await poll(30000);
    expect(result.current.data).toEqual({ state: "ready" });
    expect(axios.get).toHaveBeenCalledTimes(4);
    unmount();
  });
});
