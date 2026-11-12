// The MCP hooks on the app's real axios instance: its request and response
// interceptors and the API contract checks run for real, and only the network
// is replaced (an axios adapter that answers with Django's own bodies). This
// is the path a browser takes from a Django error envelope to the hook's
// result and the app's toast, which the page tests stub out.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import settle from "axios/unsafe/core/settle.js";
import { enqueueSnackbar } from "notistack";
import axiosInstance from "src/utils/axios";
import { handleError } from "src/utils/queryErrorHandler";

import { isMCPTurnedOff } from "../utils";
import {
  useMCPPrompts,
  useMCPResources,
  useMCPStatus,
  useMCPTools,
} from "./useMCPConfig";

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "https://offline.invalid",
}));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: vi.fn(),
  getIsRefreshing: vi.fn(),
  getRefreshToken: vi.fn(),
  getRememberMe: vi.fn(),
  processQueue: vi.fn(),
  refreshTokenRequest: vi.fn(),
  setIsRefreshing: vi.fn(),
  setSession: vi.fn(),
}));

const GATEWAY_ID = "00000000-0000-0000-0000-0000000000a1";

// Django's bodies, as tfc.utils.api_errors.build_error_envelope writes them
// for agentcc/views/gateway.py's MCP list reads (404 when the deployment runs
// with MCP off, 502 when the gateway fails).
const MCP_OFF_MESSAGE = "MCP is turned off for this deployment.";
const MCP_OFF_BODY = {
  status: false,
  type: "not_found",
  code: "gateway_mcp_disabled",
  detail: MCP_OFF_MESSAGE,
  message: MCP_OFF_MESSAGE,
  error: MCP_OFF_MESSAGE,
  result: MCP_OFF_MESSAGE,
};
const UNREACHABLE_MESSAGE = "The gateway could not be reached for MCP data.";
const UNREACHABLE_BODY = {
  status: false,
  type: "api_error",
  code: "gateway_unreachable",
  detail: UNREACHABLE_MESSAGE,
  message: UNREACHABLE_MESSAGE,
  error: UNREACHABLE_MESSAGE,
  result: UNREACHABLE_MESSAGE,
};
const OFF_STATUS = {
  enabled: false,
  reason: "gateway_mcp_disabled",
  sessions: 0,
  tools: 0,
  resources: 0,
  prompts: 0,
  servers: [],
};

// Answers every request with `status` and `body`, as JSON text, the way the
// browser adapter does: axios parses it, and `settle` rejects a non-2xx.
const requests = [];
function respondWith(status, body) {
  axiosInstance.defaults.adapter = (config) =>
    new Promise((resolve, reject) => {
      requests.push(config.url);
      settle(resolve, reject, {
        data: JSON.stringify(body),
        status,
        statusText: String(status),
        headers: { "content-type": "application/json" },
        config,
        request: {},
      });
    });
}

function errorToasts() {
  return enqueueSnackbar.mock.calls.filter(
    ([, options]) => options?.variant === "error",
  );
}

function renderQuery(useHook) {
  const client = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useHook(GATEWAY_ID), { wrapper });
}

const LIST_HOOKS = [
  ["useMCPTools", useMCPTools, "mcp-tools"],
  ["useMCPResources", useMCPResources, "mcp-resources"],
  ["useMCPPrompts", useMCPPrompts, "mcp-prompts"],
];

let originalAdapter;
beforeEach(() => {
  originalAdapter = axiosInstance.defaults.adapter;
  requests.length = 0;
  enqueueSnackbar.mockClear();
});
afterEach(() => {
  axiosInstance.defaults.adapter = originalAdapter;
});

describe("MCP reads through the real axios interceptors", () => {
  it.each(LIST_HOOKS)(
    "%s: Django's 404 gateway_mcp_disabled is an empty list and no toast",
    async (_, useHook, path) => {
      respondWith(404, MCP_OFF_BODY);
      const { result } = renderQuery(useHook);

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.data).toEqual([]);
      expect(requests).toEqual([`/agentcc/gateways/${GATEWAY_ID}/${path}/`]);
      expect(errorToasts()).toEqual([]);
    },
  );

  it.each(LIST_HOOKS)(
    "%s: Django's 502 gateway_unreachable is an error with one toast",
    async (_, useHook) => {
      respondWith(502, UNREACHABLE_BODY);
      const { result } = renderQuery(useHook);

      await waitFor(() => expect(result.current.isError).toBe(true));
      // The interceptor keeps Django's code and status on the error.
      expect(result.current.error).toMatchObject({
        code: "gateway_unreachable",
        statusCode: 502,
        result: UNREACHABLE_MESSAGE,
      });
      // A 502 is not a user-facing status, so the app's handler shows its
      // generic text; the tab itself says the read failed.
      await waitFor(() =>
        expect(errorToasts()).toEqual([
          ["Something went wrong", { variant: "error" }],
        ]),
      );
    },
  );

  it("useMCPStatus: Django's off status reads as MCP turned off", async () => {
    respondWith(200, { status: true, result: OFF_STATUS });
    const { result } = renderQuery(useMCPStatus);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(OFF_STATUS);
    expect(isMCPTurnedOff(result.current.data)).toBe(true);
    expect(errorToasts()).toEqual([]);
  });

  it("useMCPStatus: an unreachable gateway is not read as MCP turned off", async () => {
    const status = { ...OFF_STATUS, reason: "gateway_unreachable" };
    respondWith(200, { status: true, result: status });
    const { result } = renderQuery(useMCPStatus);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(isMCPTurnedOff(result.current.data)).toBe(false);
  });
});
