import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import {
  focusManager,
  QueryCache,
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  canReadHostUpdates,
  fetchHostUpdateStatus,
  normalizeHostUpdateStatus,
  STATUS_POLL_MS,
  useHostUpdateStatus,
} from "../host-update-status";
import { fixtures, responseFixture, statusFixture } from "./fixtures";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  auth: {},
  org: {},
  deployment: {},
}));
vi.mock("src/utils/axios", () => ({ default: { get: mocks.get } }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => mocks.auth }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => mocks.org,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => mocks.deployment,
}));

const clients = [];
function setup() {
  const errors = vi.fn();
  const client = new QueryClient({
    queryCache: new QueryCache({ onError: errors }),
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  wrapper.propTypes = { children: PropTypes.node };
  return { client, wrapper, errors };
}

beforeEach(() => {
  mocks.auth = {
    user: { id: "owner", org_level: 15, organization_role: "Owner" },
    authenticated: true,
    loading: false,
  };
  mocks.org = { isReady: true, currentOrganizationId: "org-a" };
  mocks.deployment = { isSuccess: true, isLoading: false, mode: "oss" };
  mocks.get.mockReset().mockResolvedValue({ data: responseFixture() });
  localStorage.clear();
  focusManager.setFocused(true);
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

describe("closed host report client", () => {
  it.each(fixtures.valid_api)(
    "accepts the generated API fixture $name",
    ({ response }) => {
      expect(normalizeHostUpdateStatus(response)).toEqual(response.result);
    },
  );
  it.each(fixtures.valid_status)(
    "accepts the closed snapshot $name",
    ({ name }) => {
      const data = statusFixture(name);
      expect(normalizeHostUpdateStatus(data)).toEqual(data.result);
    },
  );
  it.each([null, {}, { status: true }, { status: true, result: {} }])(
    "fails closed for absent or incomplete data %j",
    (value) => {
      expect(normalizeHostUpdateStatus(value)).toMatchObject({
        availability: "unavailable",
        snapshot: null,
      });
    },
  );
  it.each(["api", "snapshot"])(
    "treats an unknown %s version as unsupported",
    (kind) => {
      const data = responseFixture();
      if (kind === "api") data.result.api_schema_version = 2;
      else data.result.snapshot.schema_version = 2;
      expect(normalizeHostUpdateStatus(data)).toMatchObject({
        availability: "unsupported",
        snapshot: null,
      });
    },
  );
  it.each([
    (r) => {
      r.result.snapshot.versions.selected_release =
        "<img src=x onerror=secret()>";
    },
    (r) => {
      r.result.snapshot.discovery.reason_codes = ["/private/secret.env"];
    },
    (r) => {
      r.result.snapshot.automation.maintenance_window = {
        kind: "interval",
        timezone: "UTC",
        days_of_week: [1],
        start: "12:00",
        end: "13:00",
        command: "secret",
      };
    },
    (r) => {
      r.result.snapshot.automation.maintenance_window = {
        kind: "always",
        timezone: "bad/secret-zone",
      };
    },
    (r) => {
      r.result.snapshot.generated_at = "2026-02-30T12:00:00Z";
    },
    (r) => {
      r.result.snapshot.discovery.state = "surprise";
    },
    (r) => {
      r.result.snapshot.discovery.verification = "rejected";
    },
    (r) => {
      r.result.snapshot.download.state = "downloaded";
    },
    (r) => {
      r.result.snapshot.versions.runtime_state = "mixed";
    },
    (r) => {
      r.result.snapshot.private_path = "/secret";
    },
  ])("does not expose malformed or unsafe fields %#", (modify) => {
    const data = responseFixture();
    modify(data);
    expect(normalizeHostUpdateStatus(data)).toMatchObject({
      availability: "unavailable",
      reason_code: "malformed",
      snapshot: null,
    });
  });
  it("uses one bounded authenticated GET with cancellation and explicit organization scope", async () => {
    const controller = new AbortController();
    await fetchHostUpdateStatus({
      signal: controller.signal,
      organizationId: "org-a",
    });
    expect(mocks.get).toHaveBeenCalledExactlyOnceWith(
      "/api/host-update-status/",
      {
        signal: controller.signal,
        timeout: 5000,
        headers: { "X-Organization-Id": "org-a" },
      },
    );
  });
  it.each([
    [401, "forbidden"],
    [403, "forbidden"],
    [404, "unsupported"],
    [400, "unavailable"],
  ])(
    "closes HTTP %s without exposing error bodies",
    async (statusCode, availability) => {
      mocks.get.mockRejectedValue({
        statusCode,
        result: "/private/secret.env",
        message: "secret",
      });
      const data = await fetchHostUpdateStatus({ organizationId: "org-a" });
      expect(data).toMatchObject({ availability, snapshot: null });
      expect(JSON.stringify(data)).not.toMatch(/secret|private/);
    },
  );
});

describe("role and deployment gate", () => {
  it.each([
    ["Owner", null, true],
    ["Admin", null, true],
    ["Member", null, false],
    ["Viewer", null, false],
    ["workspace_admin", null, false],
    ["Owner", 3, false],
    ["Owner", 10, false],
    ["Member", 8, true],
    ["Member", 15, true],
    ["Admin", "8", false],
  ])("uses organization authority %s / %s", (role, level, allowed) => {
    mocks.auth.user.organization_role = role;
    mocks.auth.user.org_level = level;
    mocks.auth.user.default_workspace_role = "workspace_admin";
    expect(canReadHostUpdates(mocks.auth, mocks.org, mocks.deployment)).toBe(
      allowed,
    );
  });
  it.each(["cloud", "ee", "unknown"])(
    "does not query %s deployments",
    (mode) => {
      mocks.deployment.mode = mode;
      const { result } = renderHook(useHostUpdateStatus, setup());
      expect(result.current.allowed).toBe(false);
      expect(result.current.data).toBeUndefined();
      expect(mocks.get).not.toHaveBeenCalled();
    },
  );
  it.each(["auth", "organization", "mode", "member"])(
    "does not query before the %s gate permits it",
    (gate) => {
      if (gate === "auth") mocks.auth.loading = true;
      if (gate === "organization") mocks.org.isReady = false;
      if (gate === "mode") mocks.deployment.isSuccess = false;
      if (gate === "member") mocks.auth.user.org_level = 3;
      const { result } = renderHook(useHostUpdateStatus, setup());
      expect(result.current.allowed).toBe(false);
      expect(mocks.get).not.toHaveBeenCalled();
    },
  );
});

describe("query isolation and lifecycle", () => {
  it("shares notice/details reads without delaying unrelated queries", async () => {
    let finish;
    mocks.get.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { wrapper, client } = setup();
    const { result } = renderHook(
      () => ({
        notice: useHostUpdateStatus(),
        details: useHostUpdateStatus(),
        unrelated: useQuery({
          queryKey: ["ordinary-product-data"],
          queryFn: async () => "ready",
        }),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.unrelated.data).toBe("ready"));
    expect(result.current.notice.isLoading).toBe(true);
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(
      client
        .getQueryCache()
        .find({ queryKey: ["host-update-status"], exact: false }).options.meta,
    ).toEqual({ errorHandled: true });
    await act(async () => finish({ data: responseFixture() }));
    await waitFor(() =>
      expect(result.current.details.data?.availability).toBe("available"),
    );
  });
  it.each([401, 403, 404])(
    "clears a cached report and latches HTTP %s across remount, focus, refresh and polling",
    async (statusCode) => {
      const { wrapper, client } = setup();
      const hook = renderHook(useHostUpdateStatus, { wrapper });
      await waitFor(() =>
        expect(hook.result.current.data?.snapshot).toBeTruthy(),
      );
      mocks.get.mockRejectedValue({ statusCode, result: "secret" });
      await act(async () => hook.result.current.refresh());
      await waitFor(() =>
        expect(hook.result.current.data?.snapshot).toBeNull(),
      );
      expect(hook.result.current.canRefresh).toBe(false);
      expect(
        JSON.stringify(
          client.getQueriesData({ queryKey: ["host-update-status"] }),
        ),
      ).not.toContain("v1.2.3");
      hook.unmount();
      vi.useFakeTimers();
      const again = renderHook(useHostUpdateStatus, { wrapper });
      await act(async () => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
        again.result.current.refresh();
        await vi.advanceTimersByTimeAsync(STATUS_POLL_MS * 3);
      });
      expect(mocks.get).toHaveBeenCalledTimes(2);
    },
  );
  it.each(["user", "organization", "role", "logout", "session"])(
    "never shows the previous report after %s changes",
    async (change) => {
      const { wrapper, client } = setup();
      const { result, rerender } = renderHook(useHostUpdateStatus, { wrapper });
      await waitFor(() => expect(result.current.data?.snapshot).toBeTruthy());
      mocks.get.mockImplementation(() => new Promise(() => {}));
      if (change === "user")
        mocks.auth.user = { ...mocks.auth.user, id: "other" };
      if (change === "organization") mocks.org.currentOrganizationId = "org-b";
      if (change === "role") mocks.auth.user.org_level = 3;
      if (change === "logout") mocks.auth.authenticated = false;
      if (change === "session")
        localStorage.setItem("fai_auth_generation", "new-login");
      rerender();
      expect(result.current.data).toBeUndefined();
      expect(
        JSON.stringify(
          client.getQueriesData({ queryKey: ["host-update-status"] }),
        ),
      ).not.toContain("v1.2.3");
    },
  );
  it("cancels in-flight reads when the organization changes", async () => {
    mocks.get.mockImplementation(() => new Promise(() => {}));
    const { wrapper } = setup();
    const { rerender } = renderHook(useHostUpdateStatus, { wrapper });
    const signal = mocks.get.mock.calls[0][1].signal;
    mocks.org.currentOrganizationId = "org-b";
    rerender();
    expect(signal.aborted).toBe(true);
    expect(mocks.get.mock.calls.at(-1)[1].headers["X-Organization-Id"]).toBe(
      "org-b",
    );
  });
  it("polls only visible tabs once per minute, shares fresh cache and refetches on focus", async () => {
    vi.useFakeTimers();
    const { wrapper } = setup();
    const first = renderHook(useHostUpdateStatus, { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    renderHook(useHostUpdateStatus, { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STATUS_POLL_MS - 2);
    });
    expect(mocks.get).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2);
    });
    expect(mocks.get).toHaveBeenCalledTimes(2);
    await act(async () => {
      focusManager.setFocused(false);
      await vi.advanceTimersByTimeAsync(STATUS_POLL_MS * 3);
    });
    expect(mocks.get).toHaveBeenCalledTimes(2);
    await act(async () => {
      focusManager.setFocused(true);
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(mocks.get).toHaveBeenCalledTimes(3);
    expect(first.result.current.data?.availability).toBe("available");
  });
  it("retries a transient failure only once and replaces visible cached data with unavailable", async () => {
    const { wrapper, client, errors } = setup();
    const { result } = renderHook(useHostUpdateStatus, { wrapper });
    await waitFor(() => expect(result.current.data?.snapshot).toBeTruthy());
    mocks.get.mockRejectedValue({
      statusCode: 503,
      result: "secret raw error",
    });
    vi.useFakeTimers();
    act(() => {
      result.current.refresh();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(mocks.get).toHaveBeenCalledTimes(3);
    expect(result.current.data).toMatchObject({
      availability: "unavailable",
      snapshot: null,
    });
    expect(errors).toHaveBeenCalledTimes(1);
    expect(errors.mock.calls[0][1].options.meta.errorHandled).toBe(true);
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((q) => q.state.error),
      ),
    ).not.toContain("secret");
  });
  it("works with blocked browser storage and never persists snapshots", async () => {
    const spy = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    const writes = vi.spyOn(Storage.prototype, "setItem");
    const { result } = renderHook(useHostUpdateStatus, setup());
    await waitFor(() =>
      expect(result.current.data?.availability).toBe("available"),
    );
    expect(writes).not.toHaveBeenCalled();
    spy.mockRestore();
    writes.mockRestore();
  });
});
