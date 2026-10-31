import { afterEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { fetchHostUpdateStatus } from "../host-update-status";
import { responseFixture } from "./fixtures";

const auth = vi.hoisted(() => ({
  refresh: vi.fn(),
  session: vi.fn(),
  clear: vi.fn(),
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: auth.clear,
  getIsRefreshing: () => false,
  getRefreshToken: () => "synthetic-refresh-token",
  getRememberMe: () => true,
  processQueue: vi.fn(),
  refreshTokenRequest: auth.refresh,
  setIsRefreshing: vi.fn(),
  setSession: auth.session,
}));

const originalAdapter = axios.defaults.adapter;
afterEach(() => {
  axios.defaults.adapter = originalAdapter;
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function reject(config, status, data) {
  return Promise.reject({ config, response: { status, data, config } });
}

describe("host reporting through the real shared Axios transport", () => {
  it("keeps existing bearer refresh behavior and retries only the GET", async () => {
    auth.refresh.mockResolvedValue({ data: { access: "synthetic-new-token" } });
    const adapter = vi
      .fn()
      .mockImplementationOnce((config) =>
        reject(config, 401, {
          status: false,
          type: "authentication_error",
          code: "not_authenticated",
          detail: "Authentication required.",
          message: "Authentication required.",
          error: "Authentication required.",
          result: "Authentication required.",
        }),
      )
      .mockImplementationOnce(async (config) => ({
        status: 200,
        data: responseFixture(),
        config,
      }));
    axios.defaults.adapter = adapter;
    const data = await fetchHostUpdateStatus({ organizationId: "org-a" });
    expect(data.availability).toBe("available");
    expect(auth.refresh).toHaveBeenCalledTimes(1);
    expect(auth.session).toHaveBeenCalledWith("synthetic-new-token", "org-a");
    expect(auth.clear).not.toHaveBeenCalled();
    expect(adapter).toHaveBeenCalledTimes(2);
    for (const [config] of adapter.mock.calls) {
      expect(config.method).toBe("get");
      expect(config.url).toBe("/api/host-update-status/");
      expect(config.timeout).toBe(5000);
    }
  });
  it("preserves the existing 2FA enforcement event while removing report data", async () => {
    const event = vi.fn();
    window.addEventListener("2fa-enforcement-block", event);
    try {
      axios.defaults.adapter = (config) =>
        reject(config, 403, {
          status: false,
          type: "permission_error",
          code: "2fa_required",
          detail: "2FA required",
          message: "2FA required",
          error: "2FA required",
          result: "2FA required",
        });
      const data = await fetchHostUpdateStatus({ organizationId: "org-a" });
      expect(data).toMatchObject({ availability: "forbidden", snapshot: null });
      expect(event).toHaveBeenCalledTimes(1);
      expect(auth.refresh).not.toHaveBeenCalled();
      expect(auth.clear).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("2fa-enforcement-block", event);
    }
  });
  it("keeps SAML scope refusal closed with the common error aliases", async () => {
    const detail = "Organization selection is not authorized.";
    const adapter = vi.fn((config) =>
      reject(config, 403, {
        status: false,
        type: "permission_error",
        code: "saml_scope_conflict",
        detail,
        message: detail,
        error: detail,
        result: detail,
      }),
    );
    axios.defaults.adapter = adapter;
    expect(
      await fetchHostUpdateStatus({ organizationId: "org-a" }),
    ).toMatchObject({ availability: "forbidden", snapshot: null });
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(auth.refresh).not.toHaveBeenCalled();
  });
  it("never exposes server error aliases as report data or transport text", async () => {
    const detail = "CANARY postgres://user:password@db /private/host";
    const adapter = vi.fn((config) =>
      reject(config, 503, {
        status: false,
        type: "service_unavailable",
        code: "service_unavailable",
        detail,
        message: detail,
        error: detail,
        result: detail,
      }),
    );
    axios.defaults.adapter = adapter;
    await expect(
      fetchHostUpdateStatus({ organizationId: "org-a" }),
    ).rejects.toThrow(/^Host update reporting is unavailable\.$/);
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(auth.refresh).not.toHaveBeenCalled();
    expect(auth.clear).not.toHaveBeenCalled();
  });
  it("handles an old backend's untyped 404 without a mutation fallback", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const adapter = vi.fn((config) =>
      reject(config, 404, "<html>Not found</html>"),
    );
    axios.defaults.adapter = adapter;
    expect(
      await fetchHostUpdateStatus({ organizationId: "org-a" }),
    ).toMatchObject({ availability: "unsupported", snapshot: null });
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(auth.refresh).not.toHaveBeenCalled();
  });
  it("does not change global Axios timeout or authentication when a request times out", async () => {
    const timeout = axios.defaults.timeout;
    axios.defaults.adapter = (config) =>
      Promise.reject({
        config,
        code: "ECONNABORTED",
        message: "private transport detail",
      });
    await expect(
      fetchHostUpdateStatus({ organizationId: "org-a" }),
    ).rejects.toThrow("Host update reporting is unavailable.");
    expect(axios.defaults.timeout).toBe(timeout);
    expect(auth.refresh).not.toHaveBeenCalled();
    expect(auth.clear).not.toHaveBeenCalled();
  });
});
