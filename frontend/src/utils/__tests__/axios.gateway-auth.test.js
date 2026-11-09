import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as auth from "src/auth/context/jwt/utils";
import { resetUser } from "src/utils/Mixpanel";
import client, { endpoints } from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";

vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn(async () => "fake-recaptcha"),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// Real Axios interceptors and JWT utilities; only transport/integrations are inert.
const originalAdapter = client.defaults.adapter;
const transport = vi.fn();
const gatewayMessage =
  "Gateway authentication failed. Ask your administrator to check the gateway credentials.";
let location;
let rejectedError;
let sessionSpy;
let clearSpy;
let refreshSpy;

function rejectHttp(status, data, retry = false) {
  return async (config) => {
    if (retry) config._retry = true;
    rejectedError = new AxiosError(
      `Request failed with status code ${status}`,
      AxiosError.ERR_BAD_REQUEST,
      config,
      undefined,
      { status, config, headers: {}, data },
    );
    throw rejectedError;
  };
}

const respond = (data) => async (config) => ({
  status: 200,
  statusText: "OK",
  config,
  headers: {},
  data,
});

function gatewayEnvelope() {
  return {
    status: false,
    type: "authentication_error",
    code: "gateway_request_rejected",
    detail: gatewayMessage,
    message: gatewayMessage,
    error: gatewayMessage,
    result: gatewayMessage,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  auth.setIsRefreshing(false);
  auth.setSession("fake-valid-fa-access");
  sessionSpy = vi.spyOn(auth, "setSession");
  clearSpy = vi.spyOn(auth, "clearTokens");
  refreshSpy = vi.spyOn(auth, "refreshTokenRequest");
  location = {
    href: "http://localhost/dashboard/gateway/keys",
    pathname: "/dashboard/gateway/keys",
    replace: vi.fn(),
    assign: vi.fn(),
  };
  const browser = window;
  vi.stubGlobal(
    "window",
    new Proxy(browser, {
      get: (target, key) =>
        key === "location" ? location : Reflect.get(target, key, target),
    }),
  );
  transport.mockReset();
  transport.mockImplementation(async () => {
    throw new Error("Unexpected mocked transport request");
  });
  client.defaults.adapter = transport;
});

afterEach(() => {
  client.defaults.adapter = originalAdapter;
  vi.restoreAllMocks();
  auth.setSession(null);
  auth.clearTokens();
  auth.setIsRefreshing(false);
  vi.unstubAllGlobals();
});

function expectSessionUntouched(href) {
  expect(refreshSpy).not.toHaveBeenCalled();
  expect(sessionSpy).not.toHaveBeenCalled();
  expect(clearSpy).not.toHaveBeenCalled();
  expect(resetUser).not.toHaveBeenCalled();
  expect(localStorage.getItem("accessToken")).toBe("fake-valid-fa-access");
  expect(client.defaults.headers.common.Authorization).toBe(
    "Bearer fake-valid-fa-access",
  );
  expect(location.href).toBe(href);
  expect(location.replace).not.toHaveBeenCalled();
  expect(location.assign).not.toHaveBeenCalled();
  expect(auth.getIsRefreshing()).toBe(false);
  expect(transport).toHaveBeenCalledTimes(1);
}

describe("gateway credential rejection is not FA session expiry", () => {
  it.each([
    [false, false],
    [false, true],
    [true, false],
    [true, true],
  ])(
    "preserves the session and original error (rememberMe=%s, refresh token=%s)",
    async (remembered, hasRefresh) => {
      auth.setRememberMe(remembered);
      if (hasRefresh) auth.setRefreshToken("fake-valid-fa-refresh");
      const envelope = gatewayEnvelope();
      transport.mockImplementationOnce(rejectHttp(401, envelope));
      const href = location.href;

      const error = await client
        .post(endpoints.gateway.syncApiKeys, {})
        .catch((err) => err);

      expectSessionUntouched(href);
      expect(error).toBe(rejectedError);
      expect(error.response.data).toBe(envelope);
      expect(error.config._retry).toBeUndefined();
      expect(error.config._sharedRetry).toBeUndefined();
      expect(error.config.headers.Authorization).toBe(
        "Bearer fake-valid-fa-access",
      );
      expect(auth.getRememberMe()).toBe(remembered);
      expect(auth.getRefreshToken()).toBe(
        hasRefresh ? "fake-valid-fa-refresh" : null,
      );
      expect(getRequestErrorMessage(error)).toBe(gatewayMessage);
    },
  );

  it("does not drop the session or retry anonymously on a shared-link page", async () => {
    location.pathname = "/shared/fake-link";
    location.href = "http://localhost/shared/fake-link";
    transport.mockImplementationOnce(rejectHttp(401, gatewayEnvelope()));
    const error = await client
      .post(endpoints.gateway.syncApiKeys, {})
      .catch((err) => err);

    expectSessionUntouched("http://localhost/shared/fake-link");
    expect(error).toBe(rejectedError);
    expect(error.config._sharedRetry).toBeUndefined();
    expect(error.config._retry).toBeUndefined();
    expect(error.config.headers.Authorization).toBe(
      "Bearer fake-valid-fa-access",
    );
  });

  it("preserves an already-retried gateway error unchanged", async () => {
    transport.mockImplementationOnce(rejectHttp(401, gatewayEnvelope(), true));
    const error = await client
      .post(endpoints.gateway.syncApiKeys, {})
      .catch((err) => err);

    expectSessionUntouched("http://localhost/dashboard/gateway/keys");
    expect(error).toBe(rejectedError);
    expect(error.config._retry).toBe(true);
  });
});

describe("other responses retain existing handling", () => {
  it.each([
    ["no code", {}],
    ["FA expired token", { code: "authentication_failed" }],
    ["FA unauthenticated", { code: "not_authenticated" }],
    ["any other code", { code: "other_failure" }],
    ["provider code on 401", { code: "provider_authentication_failed" }],
    ["nested gateway code", { result: { code: "gateway_request_rejected" } }],
    ["gateway message alone", { message: gatewayMessage }],
  ])("refreshes an ordinary 401 with %s", async (_label, data) => {
    auth.setRememberMe(true);
    auth.setRefreshToken("fake-valid-fa-refresh");
    transport
      .mockImplementationOnce(rejectHttp(401, data))
      .mockImplementationOnce(respond({ access: "fake-refreshed-access" }))
      .mockImplementationOnce(respond({ status: true }));

    await expect(
      client.post(endpoints.gateway.syncApiKeys, {}),
    ).resolves.toMatchObject({
      data: { status: true },
    });
    expect(refreshSpy).toHaveBeenCalledTimes(1);
    expect(transport.mock.calls.map(([config]) => config.url)).toEqual([
      endpoints.gateway.syncApiKeys,
      endpoints.auth.refreshToken,
      endpoints.gateway.syncApiKeys,
    ]);
    expect(rejectedError.config._retry).toBe(true);
    expect(localStorage.getItem("accessToken")).toBe("fake-refreshed-access");
    expect(clearSpy).not.toHaveBeenCalled();
    expect(resetUser).not.toHaveBeenCalled();
  });

  it("still logs out for an FA authentication failure without refresh credentials", async () => {
    transport.mockImplementationOnce(
      rejectHttp(401, { code: "authentication_failed" }),
    );
    await expect(
      client.post(endpoints.gateway.syncApiKeys, {}),
    ).rejects.toThrow("No refresh token");

    expect(refreshSpy).toHaveBeenCalledTimes(1);
    expect(sessionSpy).toHaveBeenCalledWith(null);
    expect(clearSpy).toHaveBeenCalled();
    expect(resetUser).toHaveBeenCalled();
    expect(localStorage.getItem("accessToken")).toBeNull();
    expect(location.href).toBe("/auth/jwt/login");
  });

  it.each([400, 403, 409, 429, 503])(
    "normalizes HTTP %s with the gateway code as an ordinary non-401 error",
    async (status) => {
      const envelope = gatewayEnvelope();
      transport.mockImplementationOnce(rejectHttp(status, envelope));
      await expect(
        client.post(endpoints.gateway.syncApiKeys, {}),
      ).rejects.toMatchObject({
        ...envelope,
        statusCode: status,
        transportCode: AxiosError.ERR_BAD_REQUEST,
      });
      expectSessionUntouched("http://localhost/dashboard/gateway/keys");
      expect(rejectedError.config._retry).toBeUndefined();
    },
  );

  it("preserves provider HTTP 400 authentication handling", async () => {
    transport.mockImplementationOnce(
      rejectHttp(400, {
        status: false,
        code: "provider_authentication_failed",
        result: "Provider rejected the credential.",
      }),
    );
    await expect(
      client.post(endpoints.gateway.syncApiKeys, {}),
    ).rejects.toMatchObject({
      statusCode: 400,
      code: "provider_authentication_failed",
      result: "Provider rejected the credential.",
    });
    expectSessionUntouched("http://localhost/dashboard/gateway/keys");
  });

  it("leaves the provider discovery HTTP 200 empty-model verdict intact", async () => {
    const data = {
      status: true,
      result: {
        models: [],
        error: "Provider rejected the credential.",
        code: "provider_authentication_failed",
      },
    };
    transport.mockImplementationOnce(respond(data));
    await expect(
      client.get(endpoints.gateway.syncApiKeys),
    ).resolves.toMatchObject({ data });
    expectSessionUntouched("http://localhost/dashboard/gateway/keys");
  });
});
