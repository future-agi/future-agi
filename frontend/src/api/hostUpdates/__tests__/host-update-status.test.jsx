import PropTypes from "prop-types";
import { ApiHostUpdateStatusListResponse } from "src/generated/api-contracts/api.zod";
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
  automaticNoticeForStatus,
  humanBytes,
  humanDuration,
  canReadHostUpdates,
  fetchHostUpdateStatus,
  normalizeHostUpdateStatus,
  STATUS_POLL_MS,
  useHostUpdateStatus,
} from "../host-update-status";
import {
  fixtures,
  projectedStatusFixtures,
  responseFixture,
  restoreResponse,
  statusFixture,
} from "./fixtures";

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
    (r) => {
      r.result.snapshot.safety = null;
    },
    (r) => {
      r.result.snapshot.operation = null;
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

describe("restore-backed read-only notices", () => {
  it.each(projectedStatusFixtures)(
    "keeps producer/reader $name above prior restoration in the shipping client",
    ({ name, response }) => {
      const result = normalizeHostUpdateStatus(response);
      expect(result).toEqual(response.result);
      expect(ApiHostUpdateStatusListResponse.parse(response)).toEqual(response);
      expect(result.snapshot.last_run.restore_result).toBe("verified");
      const text = automaticNoticeForStatus(result).text;
      expect(text).toMatch(
        name === "verifying"
          ? /verifying readiness and stability/
          : /^Notify-only for this installation\./,
      );
      expect(text).not.toMatch(
        /Update to .* failed; restored|Restore discards/,
      );
    },
  );
  it("accepts the complete additive wire without losing the failed forward outcome", () => {
    const data = restoreResponse();
    expect(normalizeHostUpdateStatus(data)).toEqual(data.result);
    expect(automaticNoticeForStatus(data.result).text).toContain(
      "Update to v1.52.0 failed; restored v1.51.0; v1.52.0 will not be retried automatically.",
    );
  });
  it("does not treat terminal restoration's deliberately unknown progress freshness as stale", () => {
    const data = restoreResponse();
    data.result.progress_freshness = "unknown";
    expect(
      automaticNoticeForStatus(normalizeHostUpdateStatus(data)).text,
    ).toMatch(/^Update to v1\.52\.0 failed; restored v1\.51\.0/);
  });
  it.each([
    ["restoring", "running", "restoring_volumes"],
    ["restored", "verified", "restored"],
    ["recovery_required", "recovery_required", "restore_quiescing"],
  ])(
    "accepts a complete %s operation through the shipping generated contract",
    (state, restore, phase) => {
      const data = restoreResponse();
      const s = data.result.snapshot;
      Object.assign(s.operation, { state, phase });
      s.safety.restore_state = restore;
      s.last_run.restore_result = restore;
      if (restore !== "verified") {
        Object.assign(s.versions, {
          running_release: null,
          runtime_state: "unknown",
        });
        s.last_run.effective_version = null;
      }
      expect(ApiHostUpdateStatusListResponse.safeParse(data).success).toBe(
        true,
      );
      expect(normalizeHostUpdateStatus(data)).toEqual(data.result);
    },
  );
  it.each(["safety", "last_run"])(
    "generated Zod rejects every partial %s restore group without stripping it into legacy data",
    (section) => {
      const complete = restoreResponse();
      const legacy = responseFixture().result.snapshot[section];
      const additions = Object.keys(complete.result.snapshot[section]).filter(
        (key) => !Object.hasOwn(legacy || {}, key),
      );
      // last_run is null in the legacy fixture; these seven fields are the
      // frozen legacy run, not part of the additive recovery group.
      const keys =
        section === "last_run"
          ? additions.filter(
              (key) =>
                ![
                  "kind",
                  "automatic",
                  "outcome",
                  "from_version",
                  "target_version",
                  "finished_at",
                  "fallback_outcome",
                ].includes(key),
            )
          : additions;
      expect(keys.length).toBe(section === "safety" ? 9 : 4);
      expect(ApiHostUpdateStatusListResponse.parse(complete)).toEqual(complete);
      for (let mask = 1; mask < (1 << keys.length) - 1; mask += 1) {
        const partial = structuredClone(complete);
        keys.forEach((key, index) => {
          if (!(mask & (1 << index)))
            delete partial.result.snapshot[section][key];
        });
        expect(
          ApiHostUpdateStatusListResponse.safeParse(partial).success,
          `${section} partial restore mask ${mask}`,
        ).toBe(false);
        expect(normalizeHostUpdateStatus(partial).reason_code).toBe(
          "malformed",
        );
      }
      const withoutGroup = structuredClone(complete);
      keys.forEach((key) => {
        delete withoutGroup.result.snapshot[section][key];
      });
      expect(ApiHostUpdateStatusListResponse.parse(withoutGroup)).toEqual(
        withoutGroup,
      );
    },
  );
  it.each(["safety", "last_run", "operation", "versions"])(
    "generated Zod rejects unknown %s fields instead of silently stripping them",
    (section) => {
      const data = restoreResponse();
      data.result.snapshot[section].private_path = "/private/never-public";
      expect(ApiHostUpdateStatusListResponse.safeParse(data).success).toBe(
        false,
      );
    },
  );
  it("shows recovery even while disabled, and never claims an installed version", () => {
    const data = restoreResponse();
    Object.assign(data.result.snapshot.automation, {
      enabled: false,
      state: "disabled",
      next_check_at: null,
      next_action_at: null,
    });
    data.result.snapshot.operation.state = "recovery_required";
    data.result.snapshot.safety.restore_state = "recovery_required";
    const text = automaticNoticeForStatus(data.result).text;
    expect(text).toContain("Installed version is unverified");
    expect(text).toContain("Keep the backup and safety scheduler");
    expect(normalizeHostUpdateStatus(data).reason_code).toBe("malformed");
    Object.assign(data.result.snapshot.versions, {
      running_release: null,
      runtime_state: "unknown",
      components: [],
    });
    data.result.snapshot.last_run.effective_version = null;
    expect(normalizeHostUpdateStatus(data)).toEqual(data.result);
  });
  it("keeps failed restoration actionable instead of describing an image-only rollback", () => {
    const data = restoreResponse();
    data.result.snapshot.safety.restore_state = "failed";
    expect(automaticNoticeForStatus(data.result).text).toContain(
      "Installed version is unverified",
    );
  });
  it("labels stale recovery progress as historical rather than current availability", () => {
    const data = restoreResponse();
    data.result.progress_freshness = "stale";
    data.result.snapshot.safety.restore_state = "running";
    expect(automaticNoticeForStatus(data.result).text).toMatch(
      /^Last host report \(stale; current state unknown\):/,
    );
  });
  it.each([
    "migration_exceeds_window",
    "insufficient_disk_for_migration",
    "backup_exceeds_window",
    "insufficient_disk_for_backup",
  ])("explains %s with time and disk estimates", (reason) => {
    const data = restoreResponse();
    data.result.snapshot.last_run = null;
    Object.assign(data.result.snapshot.discovery, {
      state: "blocked",
      reason_codes: [reason],
    });
    data.result.snapshot.safety.estimates = {
      backup_seconds: 120,
      migration_seconds: 3600,
      restore_seconds: 180,
      total_seconds: 7200,
      extra_disk_bytes: 1024,
      backup_required_free_bytes: 1073741824,
      docker_required_free_bytes: 2147483648,
      protected_restore_reserve_bytes: 1073741824,
      forward_seconds: 5000,
      recovery_seconds: 2200,
      migration_deadline_seconds: 3600,
      migration_stall_timeout_seconds: 300,
    };
    expect(normalizeHostUpdateStatus(data)).toEqual(data.result);
    const text = automaticNoticeForStatus(data.result).text;
    expect(text).toContain("Notify-only for this installation");
    expect(text).toContain("backup 2 min, migration 1.0 h, restore 3 min");
    expect(text).toContain("Docker free space 2.0 GiB");
  });
  it.each([
    "checking",
    "downloading",
    "backing_up",
    "applying",
    "verifying",
    "rolling_back",
  ])(
    "prioritizes current %s activity over a historical restoration",
    (state) => {
      const data = restoreResponse();
      const snapshot = data.result.snapshot;
      Object.assign(snapshot.operation, {
        state,
        kind: "apply",
        phase: "target_verifying",
      });
      snapshot.discovery.candidate_version = "v1.53.0";
      // A prior admission refusal must not hide the operation now in progress.
      snapshot.discovery.reason_codes = ["migration_exceeds_window"];
      const result = normalizeHostUpdateStatus(data);
      expect(result).toEqual(data.result);
      const text = automaticNoticeForStatus(result).text;
      expect(text.toLowerCase()).toContain(
        state === "rolling_back" ? "prior images" : state.replaceAll("_", " "),
      );
      expect(text).not.toMatch(
        /Update to v1\.52\.0 failed|Notify-only|Restore discards/,
      );
    },
  );
  it.each([
    "migration_exceeds_window",
    "insufficient_disk_for_migration",
    "backup_exceeds_window",
    "insufficient_disk_for_backup",
    "restore_capacity_unknown",
  ])(
    "prioritizes a new release's %s refusal over a historical restoration",
    (reason) => {
      const data = restoreResponse();
      Object.assign(data.result.snapshot.discovery, {
        state: "blocked",
        candidate_version: "v1.53.0",
        reason_codes: [reason],
      });
      const result = normalizeHostUpdateStatus(data);
      expect(result).toEqual(data.result);
      const text = automaticNoticeForStatus(result).text;
      expect(text).toMatch(/^Notify-only for this installation\./);
      expect(text).not.toMatch(/Update to v1\.52\.0 failed|Restore discards/);
      expect(result.snapshot.last_run.restore_result).toBe("verified");
    },
  );
  it.each([
    "restore_not_certified",
    "transition_not_rehearsed",
    "signature_rejected",
  ])(
    "does not let restoration history hide current blocked admission %s",
    (reason) => {
      const data = restoreResponse();
      Object.assign(data.result.snapshot.discovery, {
        state: "blocked",
        candidate_version: "v1.53.0",
        reason_codes: [reason],
      });
      const text = automaticNoticeForStatus(
        normalizeHostUpdateStatus(data),
      ).text;
      expect(text).toMatch(/^Notify-only for this installation\./);
      expect(text).not.toMatch(
        /Update to v1\.52\.0 failed|Run a manual update/,
      );
    },
  );
  it.each(["restoring", "recovery_required"])(
    "keeps current %s recovery above discovery refusal and historical success",
    (state) => {
      const data = restoreResponse();
      const s = data.result.snapshot;
      s.operation.state = state;
      s.safety.restore_state =
        state === "restoring" ? "running" : "recovery_required";
      Object.assign(s.versions, {
        running_release: null,
        runtime_state: "unknown",
        components: [],
      });
      s.last_run.effective_version = null;
      Object.assign(s.discovery, {
        state: "blocked",
        reason_codes: ["migration_exceeds_window"],
      });
      const text = automaticNoticeForStatus(
        normalizeHostUpdateStatus(data),
      ).text;
      expect(text).toMatch(
        state === "restoring"
          ? /restoring the cold backup/
          : /operator attention/,
      );
      expect(text).not.toMatch(/Notify-only|restored v1\.51\.0/);
    },
  );
  it.each(["migration_stalled", "disk_watermark_abort"])(
    "keeps the completed restoration honest after runtime failure %s",
    (reason) => {
      const data = restoreResponse();
      data.result.snapshot.operation.reason_codes = [reason];
      const text = automaticNoticeForStatus(
        normalizeHostUpdateStatus(data),
      ).text;
      expect(text).toContain("Update to v1.52.0 failed; restored v1.51.0");
      expect(text).not.toContain("Notify-only");
    },
  );
  it("uses discovery freshness for admission and progress freshness for forward activity", () => {
    const data = restoreResponse();
    Object.assign(data.result.snapshot.discovery, {
      state: "blocked",
      reason_codes: ["migration_exceeds_window"],
    });
    data.result.progress_freshness = "unknown";
    expect(
      automaticNoticeForStatus(normalizeHostUpdateStatus(data)).text,
    ).toMatch(/^Notify-only/);
    data.result.discovery_freshness = "stale";
    expect(
      automaticNoticeForStatus(normalizeHostUpdateStatus(data)).text,
    ).toMatch(/^Last host report \(stale; current state unknown\):/);
    data.result.snapshot.operation.state = "verifying";
    expect(
      automaticNoticeForStatus(normalizeHostUpdateStatus(data)).text,
    ).toMatch(/^Last host report \(stale; current state unknown\):/);
  });
  it.each(["idle", "succeeded"])(
    "does not describe a prior restoration as the current %s operation",
    (state) => {
      const data = restoreResponse();
      Object.assign(data.result.snapshot.operation, { state, phase: null });
      expect(
        automaticNoticeForStatus(normalizeHostUpdateStatus(data)),
      ).toBeNull();
    },
  );
  it.each([
    (versions) => {
      versions.components = [];
    },
    (versions) => {
      versions.observed_at = null;
    },
    (versions) => {
      versions.components[0].observation = "unknown";
      versions.components[0].observed_versions = [];
    },
    (versions) => {
      versions.components[0].observation = "not_deployed";
      versions.components[0].observed_versions = [];
    },
    (versions) => {
      versions.components[0].observed_versions = ["v1.50.0"];
    },
    (versions) => {
      versions.components.push(structuredClone(versions.components[0]));
    },
  ])(
    "rejects a consistent restored runtime without valid active observations %#",
    (mutate) => {
      const data = restoreResponse();
      mutate(data.result.snapshot.versions);
      expect(normalizeHostUpdateStatus(data)).toMatchObject({
        availability: "unavailable",
        reason_code: "malformed",
        snapshot: null,
      });
    },
  );
  it("rejects incomplete groups, secrets, unknown phase and nonfinite estimates", () => {
    for (const mutate of [
      (s) => {
        delete s.safety.guidance_id;
      },
      (s) => {
        s.safety.archive_path = "/secret";
      },
      (s) => {
        s.operation.phase = "guess_recovery";
      },
      (s) => {
        s.last_run.effective_version = "/private";
      },
      (s) => {
        s.safety.estimates = { total_seconds: Infinity };
      },
    ]) {
      const data = restoreResponse();
      mutate(data.result.snapshot);
      expect(normalizeHostUpdateStatus(data)).toMatchObject({
        availability: "unavailable",
        reason_code: "malformed",
        snapshot: null,
      });
    }
  });
  it("formats conservative human-readable budgets without coercion", () => {
    expect(humanDuration(61)).toBe("2 min");
    expect(humanDuration("61")).toBe("unknown");
    expect(humanBytes(1024)).toBe("1.0 KiB");
    expect(humanBytes(-1)).toBe("unknown");
  });
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
