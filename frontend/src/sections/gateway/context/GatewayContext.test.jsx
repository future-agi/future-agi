import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
  onlineManager,
} from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes, useLocation } from "react-router-dom";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { GatewayProvider, normalizeGateway } from "./GatewayContext";
import { useGatewayContext } from "./useGatewayContext";
import GatewayGuard from "../components/GatewayGuard";

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: vi.fn() } };
});

describe("normalizeGateway", () => {
  it("maps canonical snake_case gateway fields to UI field names", () => {
    const normalized = normalizeGateway({
      id: "default",
      name: "Agent Command Center Gateway",
      base_url: "http://localhost:8080/v1",
      provider_count: 2,
      model_count: 5,
      last_health_check: "2026-05-25T01:02:03Z",
      status: "healthy",
    });

    expect(normalized).toMatchObject({
      id: "default",
      name: "Agent Command Center Gateway",
      baseUrl: "http://localhost:8080/v1",
      providerCount: 2,
      modelCount: 5,
      lastHealthCheck: "2026-05-25T01:02:03Z",
      status: "healthy",
    });
    expect(normalized.base_url).toBe("http://localhost:8080/v1");
  });

  it("reads whether this release runs console test requests, unknown when not sent", () => {
    expect(
      normalizeGateway({ id: "default", delegated_playground_available: false })
        .delegatedPlaygroundAvailable,
    ).toBe(false);
    expect(
      normalizeGateway({ id: "default", delegated_playground_available: true })
        .delegatedPlaygroundAvailable,
    ).toBe(true);
    expect(
      normalizeGateway({ id: "default" }).delegatedPlaygroundAvailable,
    ).toBeUndefined();
  });

  it("preserves existing camelCase values when both shapes are present", () => {
    const normalized = normalizeGateway({
      baseUrl: "https://ui.example/v1",
      base_url: "https://api.example/v1",
      providerCount: 7,
      provider_count: 2,
      modelCount: 11,
      model_count: 5,
      lastHealthCheck: "2026-05-25T04:05:06Z",
      last_health_check: "2026-05-25T01:02:03Z",
    });

    expect(normalized).toMatchObject({
      baseUrl: "https://ui.example/v1",
      providerCount: 7,
      modelCount: 11,
      lastHealthCheck: "2026-05-25T04:05:06Z",
    });
  });
});

const requestedUrl = "/dashboard/gateway/logs?status=error#request-42";
const gatewayResult = {
  result: [{ id: "default", base_url: "https://gateway.example.test/v1" }],
};
const clients = [];
let listRequest;
let managedAnswer;

// eslint-disable-next-line react/prop-types
function ContextReads({ onRefresh }) {
  const context = useGatewayContext();
  return (
    <>
      <div data-testid="gateway-context">
        {JSON.stringify({
          gateway: context.gateway,
          gatewayId: context.gatewayId,
          gateways: context.gateways,
          isLoading: context.isLoading,
          isRefreshing: context.isRefreshing,
          hasError: Boolean(context.error),
          managedOffered: context.managedOffered,
          hosted: context.hosted,
          deploymentModeConfirmed: context.deploymentModeConfirmed,
          canRetryDeploymentMode:
            typeof context.retryDeploymentMode === "function",
        })}
      </div>
      {onRefresh && (
        <button
          type="button"
          onClick={() => onRefresh(context.refreshGateways())}
        >
          Refresh gateways
        </button>
      )}
    </>
  );
}

function RequestedPage() {
  const location = useLocation();
  return (
    <>
      <h2>Requested gateway logs</h2>
      <div>{`${location.pathname}${location.search}${location.hash}`}</div>
    </>
  );
}

const readContext = () =>
  JSON.parse(screen.getByTestId("gateway-context").textContent);
const gatewayCalls = () =>
  axios.get.mock.calls.filter(([url]) => url === endpoints.gateway.list);

function renderProvider({ onRefresh, client } = {}) {
  const queryClient =
    client ||
    new QueryClient({
      // Deliberately leave retries enabled globally: only this query should opt out.
      defaultOptions: { queries: { retry: 3, retryDelay: 10 } },
    });
  if (!clients.includes(queryClient)) clients.push(queryClient);
  return {
    client: queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <HelmetProvider>
          <GatewayProvider>
            <ContextReads onRefresh={onRefresh} />
            <Routes>
              <Route
                path="/dashboard/gateway/logs"
                element={
                  <GatewayGuard>
                    <RequestedPage />
                  </GatewayGuard>
                }
              />
            </Routes>
          </GatewayProvider>
        </HelmetProvider>
      </QueryClientProvider>,
    ),
  };
}

async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("GatewayProvider slow-start recovery", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", requestedUrl);
    managedAnswer = { available: false };
    listRequest = vi.fn().mockResolvedValue({ data: gatewayResult });
    axios.get.mockReset();
    axios.get.mockImplementation((url, config) => {
      if (url === endpoints.gateway.list) return listRequest(config);
      if (url === endpoints.gateway.managedModels) {
        if (managedAnswer === "pending") return new Promise(() => {});
        if (managedAnswer instanceof Error)
          return Promise.reject(managedAnswer);
        return Promise.resolve({ data: { result: managedAnswer } });
      }
      if (url === endpoints.settings.v2.deploymentInfo) {
        return Promise.resolve({ data: { result: { mode: "cloud" } } });
      }
      throw new Error(`unmocked GET ${url}`);
    });
  });

  afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    focusManager.setFocused(undefined);
    onlineManager.setOnline(true);
    vi.useRealTimers();
    window.history.replaceState({}, "", "/");
  });

  it("bounds a stalled list read, waits for explicit Retry, and opens the intended page", async () => {
    vi.useFakeTimers();
    let finishRetry;
    listRequest
      .mockImplementationOnce(
        (config) =>
          new Promise((_resolve, reject) => {
            // The mock adapter honors the timeout supplied by the real query.
            if (config?.timeout) {
              setTimeout(() => reject(new Error("timeout")), config.timeout);
            }
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishRetry = resolve;
          }),
      );
    const { client, unmount } = renderProvider();
    expect(screen.getByTestId("gateway-guard-loading")).toBeInTheDocument();
    const config = gatewayCalls()[0][1];
    expect(config).toMatchObject({
      timeout: 30000,
      signal: expect.any(AbortSignal),
    });
    expect(config.signal.aborted).toBe(false);
    await advance(29999);
    expect(
      screen.queryByRole("button", { name: "Retry" }),
    ).not.toBeInTheDocument();
    await advance(2);
    expect(screen.getByText("The gateway request failed.")).toBeInTheDocument();
    expect(client.getQueryState(["agentcc-gateways"]).fetchFailureCount).toBe(
      1,
    );
    await advance(60000);
    act(() => {
      focusManager.setFocused(false);
      onlineManager.setOnline(false);
    });
    await act(async () => {
      focusManager.setFocused(true);
      onlineManager.setOnline(true);
    });
    await advance(100);
    expect(gatewayCalls()).toHaveLength(1);
    // Revisiting the page must not silently restart the failed cold read either.
    unmount();
    renderProvider({ client });
    await advance(100);
    expect(gatewayCalls()).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await advance(1);
    const retry = screen.getByRole("button", { name: "Retrying..." });
    expect(retry).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Checking for an available gateway...",
    );
    expect(readContext().isRefreshing).toBe(true);
    fireEvent.click(retry);
    expect(gatewayCalls()).toHaveLength(2);
    expect(
      `${window.location.pathname}${window.location.search}${window.location.hash}`,
    ).toBe(requestedUrl);
    await act(async () => {
      finishRetry({ data: gatewayResult });
    });
    await advance(1);
    expect(
      screen.getByRole("heading", { name: "Requested gateway logs" }),
    ).toBeInTheDocument();
    expect(screen.getByText(requestedUrl)).toBeInTheDocument();
    expect(readContext()).toMatchObject({
      isRefreshing: false,
      hasError: false,
    });
  });

  it("cancels the query signal when the provider unmounts", () => {
    listRequest.mockImplementation(() => new Promise(() => {}));
    const { unmount } = renderProvider();
    const config = gatewayCalls()[0][1];
    expect(config?.signal).toBeInstanceOf(AbortSignal);
    expect(config.signal.aborted).toBe(false);
    unmount();
    expect(config.signal.aborted).toBe(true);
  });

  it("returns a refresh promise and keeps a cached gateway visible during a failed refresh", async () => {
    const onRefresh = vi.fn();
    renderProvider({ onRefresh });
    await screen.findByRole("heading", { name: "Requested gateway logs" });
    await waitFor(() =>
      expect(readContext()).toMatchObject({
        gateway: { id: "default", baseUrl: "https://gateway.example.test/v1" },
        gatewayId: "default",
        gateways: [
          { id: "default", baseUrl: "https://gateway.example.test/v1" },
        ],
        isLoading: false,
        isRefreshing: false,
        hasError: false,
        hosted: true,
        deploymentModeConfirmed: true,
        canRetryDeploymentMode: true,
      }),
    );
    let failRefresh;
    listRequest.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          failRefresh = reject;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Refresh gateways" }));
    const refreshPromise = onRefresh.mock.calls[0][0];
    expect(refreshPromise).toBeInstanceOf(Promise);
    const settled = vi.fn();
    refreshPromise.then(settled);
    await waitFor(() => expect(readContext().isRefreshing).toBe(true));
    expect(settled).not.toHaveBeenCalled();
    expect(
      screen.getByRole("heading", { name: "Requested gateway logs" }),
    ).toBeInTheDocument();
    await act(async () => {
      failRefresh(new Error("background read failed"));
      await refreshPromise;
    });
    await waitFor(() =>
      expect(readContext()).toMatchObject({
        isRefreshing: false,
        hasError: true,
      }),
    );
    expect(settled).toHaveBeenCalledTimes(1);
    expect(gatewayCalls()).toHaveLength(2);
    expect(
      screen.getByRole("heading", { name: "Requested gateway logs" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("gateway-guard-empty")).not.toBeInTheDocument();
  });

  it.each(["focus", "reconnect"])(
    "recovers cached gateway metadata on %s after a background failure",
    async (event) => {
      const onRefresh = vi.fn();
      const { client } = renderProvider({ onRefresh });
      await screen.findByRole("heading", { name: "Requested gateway logs" });
      await waitFor(() => expect(readContext().isRefreshing).toBe(false));

      listRequest.mockRejectedValueOnce({
        statusCode: 400,
        message: "Gateway returned 503",
      });
      fireEvent.click(screen.getByRole("button", { name: "Refresh gateways" }));
      await act(async () => {
        await onRefresh.mock.calls[0][0];
      });
      await waitFor(() => expect(readContext().hasError).toBe(true));
      expect(client.getQueryState(["agentcc-gateways"])).toMatchObject({
        status: "error",
        data: gatewayResult,
        fetchStatus: "idle",
      });
      expect(gatewayCalls()).toHaveLength(2);
      expect(
        screen.getByRole("heading", { name: "Requested gateway logs" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Retry" }),
      ).not.toBeInTheDocument();

      let finishRecovery;
      listRequest.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishRecovery = resolve;
          }),
      );
      const setActive =
        event === "focus"
          ? (active) => focusManager.setFocused(active)
          : (active) => onlineManager.setOnline(active);
      act(() => setActive(false));
      await act(async () => setActive(true));
      await waitFor(() => expect(gatewayCalls()).toHaveLength(3));
      await waitFor(() => expect(readContext().isRefreshing).toBe(true));
      expect(readContext().gateway).toMatchObject({
        id: "default",
        baseUrl: "https://gateway.example.test/v1",
      });
      expect(
        screen.getByRole("heading", { name: "Requested gateway logs" }),
      ).toBeInTheDocument();

      await act(async () => {
        finishRecovery({
          data: {
            result: [{ ...gatewayResult.result[0], provider_count: 3 }],
          },
        });
      });
      await waitFor(() =>
        expect(readContext()).toMatchObject({
          gateway: { id: "default", providerCount: 3 },
          isRefreshing: false,
          hasError: false,
        }),
      );
      expect(client.getQueryState(["agentcc-gateways"]).status).toBe("success");
      expect(screen.getByText(requestedUrl)).toBeInTheDocument();
      expect(gatewayCalls()).toHaveLength(3);
    },
  );

  it.each([
    ["an empty list", []],
    ["a gateway without an ID", [{}]],
  ])(
    "keeps failed reads manual-only when the cache contains %s",
    async (_label, result) => {
      vi.useFakeTimers();
      const onRefresh = vi.fn();
      listRequest.mockResolvedValueOnce({ data: { result } });
      renderProvider({ onRefresh });
      await advance(1);
      expect(readContext().gatewayId).toBeNull();
      expect(readContext().hasError).toBe(false);

      listRequest.mockRejectedValueOnce(new Error("background read failed"));
      fireEvent.click(screen.getByRole("button", { name: "Refresh gateways" }));
      await advance(1);
      expect(readContext().hasError).toBe(true);
      act(() => {
        focusManager.setFocused(false);
        onlineManager.setOnline(false);
      });
      await act(async () => {
        focusManager.setFocused(true);
        onlineManager.setOnline(true);
      });
      await advance(100);
      expect(gatewayCalls()).toHaveLength(2);
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    },
  );

  it("describes a successful empty list without a read error or a create action", async () => {
    listRequest.mockResolvedValue({ data: { result: [] } });
    renderProvider();
    expect(await screen.findByText("No gateway available")).toBeInTheDocument();
    expect(
      screen.getByText(/No gateway is available for this organization/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/couldn't load|request failed/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /create|add gateway/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(readContext()).toMatchObject({
      gateway: null,
      gatewayId: null,
      gateways: [],
      hasError: false,
    });
  });

  it.each([
    ["offered", { available: true }, true],
    ["not offered", { available: false }, false],
    ["pending", "pending", false],
    ["failed", new Error("status unavailable"), false],
  ])(
    "preserves optional managed catalog behavior when status is %s",
    async (_label, answer, offered) => {
      managedAnswer = answer;
      renderProvider();
      await screen.findByRole("heading", { name: "Requested gateway logs" });
      await waitFor(() => expect(readContext().managedOffered).toBe(offered));
      const statusCall = axios.get.mock.calls.find(
        ([url]) => url === endpoints.gateway.managedModels,
      );
      expect(statusCall[1]).toEqual({ timeout: 30000 });
    },
  );
});
