import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

import { useRequestVolume } from "../hooks/useRequestLogs";

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => api.get(...args) },
  endpoints: {
    gateway: {
      requestLogs: "/agentcc/request-logs/",
      requestLogSearch: "/agentcc/request-logs/search/",
      requestLogVolume: "/agentcc/request-logs/volume/",
    },
  },
}));

const wrapper = ({ children }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
);

const SERIES = [
  { bucket: "2026-10-21T08:00:00Z", request_count: 10, error_count: 2 },
];
// Day buckets are the viewer's days: every series asks with the zone.
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const WINDOW = {
  start: "2026-10-20T09:38:06.404Z",
  end: "2026-10-22T09:00:00.000Z",
  granularity: "hour",
};

beforeEach(() => {
  api.get.mockReset();
  api.get.mockResolvedValue({
    data: { status: true, result: { granularity: "hour", series: SERIES } },
  });
});

// The strip read the analytics usage series, which
// filters by time and API key only. GET /agentcc/request-logs/volume/ takes
// the list's own filters, so the strip counts what the table lists.
describe("useRequestVolume", () => {
  it("asks the volume endpoint with every table filter and the window", async () => {
    const { result } = renderHook(
      () =>
        useRequestVolume(
          {
            model: "gpt-4o",
            apiKeyId: "key-1",
            statusCode: "429",
            isError: "true",
            provider: "anthropic",
            page: "3",
            sort: "-cost",
            log: "log-1",
          },
          WINDOW,
        ),
      { wrapper },
    );
    await waitFor(() =>
      expect(result.current.data).toEqual({
        granularity: "hour",
        series: SERIES,
      }),
    );
    const [url, { params }] = api.get.mock.calls[0];
    expect(url).toBe("/agentcc/request-logs/volume/");
    expect(params).toEqual({
      model: "gpt-4o",
      api_key_id: "key-1",
      status_code: "429",
      is_error: "true",
      provider: "anthropic",
      ...WINDOW,
      tz: TZ,
    });
    expect(
      validateContractedRequestConfig({ url, method: "get", params }),
    ).toMatchObject({ ok: true });
  });

  it("sends the table's search as q, as the list does", async () => {
    renderHook(() => useRequestVolume({ search: "timeout" }, WINDOW), {
      wrapper,
    });
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    const [url, { params }] = api.get.mock.calls[0];
    expect(params).toEqual({ q: "timeout", ...WINDOW, tz: TZ });
    expect(
      validateContractedRequestConfig({ url, method: "get", params }),
    ).toMatchObject({ ok: true });
  });

  it("asks nothing while disabled", () => {
    renderHook(() => useRequestVolume({}, WINDOW, { enabled: false }), {
      wrapper,
    });
    expect(api.get).not.toHaveBeenCalled();
  });
});
