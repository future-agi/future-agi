import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
const gate = vi.hoisted(() => ({ enabled: true }));
vi.mock("../useMetricsEnabled", () => ({ useMetricsEnabled: () => gate }));

import axios, { endpoints } from "src/utils/axios";
import {
  metricErrorState,
  metricKeys,
  metricsApi,
  useActivateMetric,
  useCreateMetric,
  useCreateMetricSnapshot,
  useDeleteMetricSnapshot,
  useMetricDetail,
  useMetricKpi,
  useMetricPreview,
  useMetricsList,
  useRefreshMetric,
  useUpdateMetric,
  useValidateMetric,
} from "../api";

// Synthetic unit-test fixtures shaped by the current generated contracts. The
// actual Axios interceptors validate all requests and strict response schemas.
const id = "11111111-1111-4111-8111-111111111111";
const otherId = "22222222-2222-4222-8222-222222222222";
const scope = { organizationId: id, workspaceId: id, projectId: id };
const range = {
  window_start: "2026-11-01T00:00:00.000Z",
  window_end: "2026-11-08T00:00:00.000Z",
};
const args = { scope, metricId: id };
const definition = {
  id,
  project_id: id,
  name: "Test metric",
  current_version: 1,
  active_version: null,
  value_mode: "on_demand",
  snapshot_supported: false,
  snapshot_unsupported_reason: "user_snapshot_not_supported",
};
const write = {
  name: "Test metric",
  kind: "share",
  level: "user",
  user_rule: "any",
  field_spec: { inputs: [], conditions: [] },
  value_labels: { match: "Counts", other: "Doesn't count" },
  expected_version: 1,
};
const point = {
  value: 0,
  point_status: "ok",
  rate_eligible: false,
  weighted: false,
  unit: "",
  value_format: "number",
  unit_noun: "users",
  value_mode: "on_demand",
  eligible_count: 1,
  evaluated_count: 1,
  missing_count: 0,
  uncertainty: {
    confidence_level: 0.95,
    status: "not_estimable",
    reason: "unweighted_total",
    method: "",
    interval: "",
    std_error: null,
    ci_low: null,
    ci_high: null,
    effective_n: null,
    degrees_of_freedom: null,
  },
};
const provenance = {
  manifest_id: id,
  generation: 0,
  version_id: id,
  version: 1,
  definition_hash: "synthetic-hash",
  updated_at: null,
  backfill_days_done: 0,
  backfill_days_total: 0,
};
const pinned = {
  provenance,
  effective_window: { ...range, rounding: "15 minutes" },
};
const kpi = {
  ...pinned,
  current: point,
  previous: {
    ...point,
    value: null,
    point_status: "no_data",
    eligible_count: 0,
    evaluated_count: 0,
    uncertainty: { ...point.uncertainty, reason: "no_value" },
  },
  delta: null,
  delta_uncertainty: null,
};
const page = { results: [], next_cursor: null };
const drivers = {
  ...pinned,
  next_cursor: null,
  results: [
    {
      unit_id: "synthetic-user",
      status: "value",
      no_value_reason: "",
      value_number: 0,
      value_bool: false,
      value_text: null,
      inputs: {},
      inputs_bool: {},
      inputs_text: {},
      eligible_count: 1,
      evaluated_count: 1,
      missing_count: 0,
      influence: null,
    },
  ],
};
const preview = {
  ...page,
  rate_eligible: false,
  weighted: false,
  unit: "",
  value_format: "number",
  unit_noun: "conversations",
  value_mode: "materialized",
  point_status: "no_data",
  summary: "No data in this synthetic test window",
  sample_units: [],
};
const draft = {
  ...range,
  definition: { kind: "count", level: "conversation", field_spec: {} },
};
const validation = {
  valid: true,
  kind: "share",
  required_inputs: [],
  semantics: "Synthetic validation",
};
const unavailable = {
  code: "metric_unavailable",
  message: "Metric data is temporarily unavailable.",
  retryable: true,
};
const paths = endpoints.derivedMetrics;
let adapter;
let originalAdapter;
const clients = [];
const response = (config, data, status = 200, headers = {}) => ({
  config,
  data,
  status,
  headers,
  statusText: "",
});
const serve = (data, status = 200) =>
  adapter.mockImplementation(async (config) => response(config, data, status));
const fail = (data, status, headers = {}) =>
  adapter.mockImplementation(async (config) => {
    throw { config, response: response(config, data, status, headers) };
  });
const bodyOf = (config) =>
  typeof config.data === "string" ? JSON.parse(config.data) : config.data;
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
function mount(callback, initialProps) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  clients.push(client);
  const wrapper = ({ children }) =>
    createElement(QueryClientProvider, { client }, children);
  return { ...renderHook(callback, { wrapper, initialProps }), client };
}

beforeEach(() => {
  gate.enabled = true;
  originalAdapter = axios.defaults.adapter;
  adapter = vi.fn();
  axios.defaults.adapter = adapter;
  vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "true");
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  axios.defaults.adapter = originalAdapter;
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("contracted metric transport", () => {
  it.each([
    ["list", paths.list, { limit: 30, before: otherId }, page],
    ["detail", paths.detail(id), undefined, definition],
    [
      "capabilities",
      paths.capabilities,
      undefined,
      {
        sources: [],
        levels: ["conversation"],
        kinds: ["count"],
        user_rules: {},
        max_group_dims: 2,
        preview_max_days: 7,
        preview_budget_seconds: 15,
      },
    ],
    [
      "evaluators",
      paths.evaluators,
      { search: "score", limit: 20 },
      { results: [], truncated: false },
    ],
    ["kpi", paths.kpi(id), range, kpi],
    [
      "trend",
      paths.trend(id),
      { ...range, points: 30, group_by: "model" },
      { ...pinned, results: [] },
    ],
    [
      "drivers",
      paths.drivers(id),
      { ...range, after: "server-cursor", limit: 20 },
      drivers,
    ],
    ["history", paths.history(id), { before: otherId, limit: 10 }, page],
    ["fields", paths.fields(id), { context: "users" }, { results: [] }],
    ["snapshots", paths.snapshots(id), { before: otherId, limit: 10 }, page],
  ])(
    "%s uses its real GET contract and preserves its envelope",
    async (operation, url, params, data) => {
      serve(data);
      expect(await metricsApi[operation]({ ...args, params })).toEqual(data);
      const config = adapter.mock.calls[0][0];
      expect(config.method).toBe("get");
      expect(config.url).toBe(url);
      expect(config.params).toEqual(
        ["list", "evaluators"].includes(operation)
          ? { ...params, project_id: id }
          : params,
      );
      expect(config.headers.get("X-Organization-Id")).toBe(id);
      expect(config.headers.get("X-Workspace-Id")).toBe(id);
    },
  );

  it.each([
    ["create", "post", paths.list, write, definition, 201],
    ["update", "put", paths.detail(id), write, definition, 200],
    ["validate", "post", paths.validate, write, validation, 200],
    [
      "refresh",
      "post",
      paths.refresh(id),
      { confirm: true },
      { status: "queued" },
      202,
    ],
    [
      "activate",
      "post",
      paths.activate(id),
      { version: 1, expected_active_id: null },
      definition,
      200,
    ],
    [
      "createSnapshot",
      "post",
      paths.snapshots(id),
      { ...range, idempotency_key: "test-snapshot" },
      {
        id: otherId,
        metric_id: id,
        metric_version: 1,
        ...range,
        as_of: range.window_end,
        filters: [],
        status: "queued",
        kind: "snapshot",
      },
      202,
    ],
    [
      "deleteSnapshot",
      "delete",
      paths.snapshot(id, otherId),
      undefined,
      { status: "cleanup_pending" },
      202,
    ],
  ])(
    "%s preserves write semantics, version checks and queued states",
    async (operation, method, url, data, result, status) => {
      serve(result, status);
      expect(
        await metricsApi[operation]({ ...args, snapshotId: otherId, data }),
      ).toEqual(result);
      const config = adapter.mock.calls[0][0];
      expect(config.method).toBe(method);
      expect(config.url).toBe(url);
      expect(bodyOf(config)).toEqual(
        ["create", "update", "validate"].includes(operation)
          ? { ...data, project_id: id }
          : data,
      );
    },
  );

  it("uses read-POST for unit IDs and keeps zero, false and null unchanged", async () => {
    serve(drivers);
    const params = {
      ...range,
      unit_ids: ["user/one", "user?two"],
      context: "users",
    };
    const result = await metricsApi.unitValues({ ...args, params });
    expect(result).toEqual(drivers);
    expect(result.results[0]).toMatchObject({
      value_number: 0,
      value_bool: false,
      value_text: null,
    });
    const config = adapter.mock.calls[0][0];
    expect(config).toMatchObject({ method: "post", url: paths.unitValues(id) });
    expect(bodyOf(config)).toEqual(params);
    expect(config.params).toBeUndefined();
  });

  it("preserves the distinct unified catalog envelope and explicit context/level", async () => {
    const data = {
      status: true,
      result: {
        metrics: [],
        next_cursor: null,
        total: null,
        total_is_exact: false,
      },
    };
    serve(data);
    const params = {
      project_ids: id,
      cursor_mode: true,
      context: "users",
      level: "user",
      category: "metric",
    };
    expect(await metricsApi.catalog({ scope, params })).toEqual(data);
    expect(bodyOf(adapter.mock.calls[0][0])).toEqual(params);
    expect(adapter.mock.calls[0][0].method).toBe("post");
  });

  it("rejects undeclared list search rather than pretending it is server search", async () => {
    serve(page);
    await expect(
      metricsApi.list({ scope, params: { search: "unseen pages" } }),
    ).rejects.toMatchObject({
      message: expect.stringContaining("contract validation failed"),
    });
    expect(adapter).not.toHaveBeenCalled();
  });

  it("does not mix a scoped project with a different write payload", () => {
    expect(() =>
      metricsApi.create({ scope, data: { ...write, project_id: otherId } }),
    ).toThrow("does not match");
    expect(adapter).not.toHaveBeenCalled();
  });

  it.each([
    "kpi",
    "trend",
    "drivers",
    "history",
    "fields",
    "unitValues",
    "refresh",
    "createSnapshot",
    "deleteSnapshot",
  ])(
    "%s preserves a real 503 instead of an empty result",
    async (operation) => {
      fail(unavailable, 503);
      const params =
        operation === "fields"
          ? { context: "users" }
          : operation === "history"
            ? {}
            : operation === "unitValues"
              ? { ...range, unit_ids: ["user"], context: "users" }
              : range;
      const data =
        operation === "refresh"
          ? { confirm: true }
          : { ...range, idempotency_key: "snapshot" };
      await expect(
        metricsApi[operation]({ ...args, params, data, snapshotId: otherId }),
      ).rejects.toMatchObject({ ...unavailable, statusCode: 503 });
      expect(adapter).toHaveBeenCalledTimes(1);
      expect(adapter.mock.calls[0][0].url).not.toContain("/runs/");
    },
  );

  it("does not send the cache version as an unsupported read query", async () => {
    serve(kpi);
    expect(
      await metricsApi.kpi({ ...args, version: 1, params: range }),
    ).toEqual(kpi);
    expect(adapter.mock.calls[0][0].params).toEqual(range);
    await expect(
      metricsApi.kpi({ ...args, version: 2, params: range }),
    ).rejects.toMatchObject({ code: "metric_version_changed" });
  });
});

describe("nullable comparison in the generated contract", () => {
  // The backend returns previous=null when there is no previous period; the
  // regenerated contract marks it nullable, so strict validation accepts it.
  it("accepts a null previous point under strict response validation", async () => {
    const data = { ...kpi, previous: null };
    serve(data);
    const hook = mount(() => useMetricKpi({ ...args, params: range }));
    await waitFor(() => expect(hook.result.current.readState).toBe("ready"));
    expect(hook.result.current.error).toBeNull();
    expect(hook.result.current.data).toEqual(data);
    expect(hook.result.current.data.previous).toBeNull();
    expect(adapter).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["forward", "2026-11-05T09:12:00Z"],
    [null, null],
  ])(
    "accepts a %s history mode and its coverage start under strict validation",
    async (historyMode, coverageStart) => {
      const data = {
        ...kpi,
        provenance: {
          ...provenance,
          history_mode: historyMode,
          coverage_start: coverageStart,
        },
      };
      serve(data);
      const hook = mount(() => useMetricKpi({ ...args, params: range }));
      await waitFor(() => expect(hook.result.current.readState).toBe("ready"));
      expect(hook.result.current.error).toBeNull();
      expect(hook.result.current.data.provenance).toMatchObject({
        history_mode: historyMode,
        coverage_start: coverageStart,
      });
    },
  );

  it("rejects an unknown history mode under strict validation", async () => {
    serve({ ...kpi, provenance: { ...provenance, history_mode: "backfill" } });
    const hook = mount(() => useMetricKpi({ ...args, params: range }));
    await waitFor(() => expect(hook.result.current.readState).toBe("error"));
    expect(hook.result.current.error).toMatchObject({
      name: "ApiContractValidationError",
    });
  });

  it("still rejects a previous point of the wrong shape under strict validation", async () => {
    serve({ ...kpi, previous: "not-a-point" });
    const hook = mount(() => useMetricKpi({ ...args, params: range }));
    await waitFor(() => expect(hook.result.current.readState).toBe("error"));
    expect(hook.result.current.error).toMatchObject({
      name: "ApiContractValidationError",
    });
    expect(hook.result.current.data).toBeUndefined();
  });
});

describe("request identity and cancellation", () => {
  it.each([true, false])(
    "aborts and cancels a %s saved preview with the real identity",
    async (saved) => {
      const pending = deferred();
      let previewConfig;
      adapter.mockImplementation((config) => {
        if (config.url.endsWith("/cancel/"))
          return Promise.resolve(response(config, { cancelled: false }));
        previewConfig = config;
        return pending.promise;
      });
      const controller = new AbortController();
      const promise = metricsApi.preview({
        scope,
        metricId: saved ? id : undefined,
        data: saved
          ? { ...range, version: 1, request_id: otherId }
          : { ...draft, request_id: otherId },
        signal: controller.signal,
      });
      const rejected = expect(promise).rejects.toMatchObject({
        transportCode: "ERR_CANCELED",
      });
      await waitFor(() => expect(previewConfig).toBeDefined());
      controller.abort();
      await waitFor(() => expect(adapter).toHaveBeenCalledTimes(2));
      const cancelConfig = adapter.mock.calls[1][0];
      expect(cancelConfig.url).toBe(
        saved ? paths.cancelPreview(id) : paths.cancelDraftPreview,
      );
      expect(bodyOf(cancelConfig)).toEqual(
        saved
          ? { request_id: otherId }
          : { request_id: otherId, project_id: id },
      );
      expect(cancelConfig.signal).toBeUndefined();
      expect(cancelConfig.headers.get("X-Workspace-Id")).toBe(id);
      pending.resolve(response(previewConfig, preview));
      await rejected;
    },
  );

  it("does not start or cancel an already aborted preview", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      metricsApi.preview({ scope, data: draft, signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(adapter).not.toHaveBeenCalled();
  });

  it("generates a fresh request UUID per execution and removes abort listeners after success", async () => {
    serve(preview);
    const controller = new AbortController();
    await metricsApi.preview({ scope, data: draft, signal: controller.signal });
    await metricsApi.preview({ scope, data: draft });
    const ids = adapter.mock.calls.map(([config]) => bodyOf(config).request_id);
    expect(ids[0]).toMatch(/^[a-f0-9-]{36}$/);
    expect(ids[1]).not.toBe(ids[0]);
    controller.abort();
    expect(adapter).toHaveBeenCalledTimes(2);
  });

  it.each([
    [503, unavailable, "unavailable"],
    [
      409,
      { code: "preview_superseded", message: "Superseded", retryable: false },
      "superseded",
    ],
    [429, { detail: "Preview capacity is busy." }, "busy"],
  ])("keeps preview HTTP %s distinct", async (status, data, state) => {
    fail(data, status, { "retry-after": "7" });
    let error;
    try {
      await metricsApi.preview({ scope, data: draft });
    } catch (caught) {
      error = caught;
    }
    expect(error).toMatchObject({ ...data, statusCode: status });
    expect(metricErrorState(error)).toBe(state);
    if (status === 429) expect(error.retryAfterSeconds).toBe(7);
  });
});

describe("scoped hooks", () => {
  it("fails closed when rollout is off, including manual refetch and writes", async () => {
    gate.enabled = false;
    serve(page);
    const read = mount(() => useMetricsList({ scope }));
    const writeHook = mount(() => useCreateMetric({ scope }));
    expect(read.result.current.readState).toBe("idle");
    await act(async () => {
      await read.result.current.refetch();
      await expect(writeHook.result.current.mutateAsync(write)).rejects.toThrow(
        "not enabled",
      );
    });
    expect(adapter).not.toHaveBeenCalled();
    expect(read.result.current.data).toBeUndefined();
  });

  it("does not request until scope, metric and range exist", () => {
    mount(() => useMetricKpi({ metricId: id, params: range }));
    mount(() => useMetricKpi({ scope, params: range }));
    mount(() => useMetricKpi({ ...args }));
    expect(adapter).not.toHaveBeenCalled();
  });

  it("isolates cache identities by tenant, project, definition, version, window, cursor and context", () => {
    const base = {
      ...args,
      version: 1,
      params: { ...range, context: "users" },
    };
    const key = metricKeys.read("unitValues", base);
    for (const changed of [
      { scope: { ...scope, organizationId: otherId } },
      { scope: { ...scope, workspaceId: otherId } },
      { scope: { ...scope, projectId: otherId } },
      { metricId: otherId },
      { version: 2 },
      { params: { ...base.params, window_start: "2026-11-02T00:00:00Z" } },
      { params: { ...base.params, context: "sessions" } },
      { params: { ...base.params, after: "cursor" } },
    ])
      expect(
        metricKeys.read("unitValues", { ...base, ...changed }),
      ).not.toEqual(key);
  });

  it("aborts an obsolete scope read and cannot surface its late response", async () => {
    const pending = deferred();
    let first;
    adapter.mockImplementation((config) => {
      if (config.headers.get("X-Workspace-Id") === id) {
        first = config;
        return pending.promise;
      }
      return Promise.resolve(
        response(config, { ...definition, name: "Other workspace" }),
      );
    });
    const hook = mount(
      (selected) => useMetricDetail({ scope: selected, metricId: id }),
      scope,
    );
    await waitFor(() => expect(first).toBeDefined());
    hook.rerender({ ...scope, workspaceId: otherId });
    await waitFor(() =>
      expect(hook.result.current.data?.name).toBe("Other workspace"),
    );
    expect(first.signal.aborted).toBe(true);
    await act(async () => {
      pending.resolve(response(first, definition));
    });
    expect(hook.result.current.data.name).toBe("Other workspace");
  });

  it("keeps the list and each on-demand row independent", async () => {
    const pending = deferred();
    let userRead;
    adapter.mockImplementation((config) => {
      if (config.url === paths.list)
        return Promise.resolve(
          response(config, {
            results: [definition, { ...definition, id: otherId }],
            next_cursor: null,
          }),
        );
      if (config.url === paths.kpi(otherId)) {
        userRead = config;
        return pending.promise;
      }
      return Promise.resolve(response(config, kpi));
    });
    const hook = mount(() => ({
      list: useMetricsList({ scope }),
      first: useMetricKpi({ ...args, params: range }),
      user: useMetricKpi({ scope, metricId: otherId, params: range }),
    }));
    await waitFor(() =>
      expect(hook.result.current.first.readState).toBe("ready"),
    );
    expect(hook.result.current.list.data.results).toHaveLength(2);
    expect(hook.result.current.first.data.current.value).toBe(0);
    expect(hook.result.current.user.readState).toBe("loading");
    expect(hook.result.current.user.data).toBeUndefined();
    hook.unmount();
    expect(userRead.signal.aborted).toBe(true);
    pending.resolve(response(userRead, kpi));
  });

  it("hides a previous value when a refetch becomes unavailable", async () => {
    serve(kpi);
    const hook = mount(() => useMetricKpi({ ...args, params: range }));
    await waitFor(() => expect(hook.result.current.data).toEqual(kpi));
    fail(unavailable, 503);
    await act(async () => {
      await hook.result.current.refetch();
    });
    await waitFor(() =>
      expect(hook.result.current.readState).toBe("unavailable"),
    );
    expect(hook.result.current.data).toBeUndefined();
    expect(hook.result.current.error).toMatchObject({
      ...unavailable,
      statusCode: 503,
    });
    expect(adapter).toHaveBeenCalledTimes(2);
  });

  it("requests the actual next page without previous-page placeholders", async () => {
    adapter.mockImplementation(async (config) =>
      response(
        config,
        config.params?.before
          ? page
          : { results: [definition], next_cursor: otherId },
      ),
    );
    const hook = mount((params) => useMetricsList({ scope, params }), {
      limit: 1,
    });
    await waitFor(() =>
      expect(hook.result.current.data?.next_cursor).toBe(otherId),
    );
    hook.rerender({ limit: 1, before: hook.result.current.data.next_cursor });
    expect(hook.result.current.data).toBeUndefined();
    await waitFor(() => expect(hook.result.current.data).toEqual(page));
    expect(adapter.mock.calls[1][0].params).toEqual({
      project_id: id,
      limit: 1,
      before: otherId,
    });
  });

  it.each(["replacement", "unmount", "disabled"])(
    "cancels a preview on %s, without exposing its stale body",
    async (reason) => {
      const pending = deferred();
      let first;
      adapter.mockImplementation((config) => {
        if (config.url.endsWith("/cancel/"))
          return Promise.resolve(response(config, { cancelled: true }));
        if (!first) {
          first = config;
          return pending.promise;
        }
        return Promise.resolve(response(config, preview));
      });
      const hook = mount((options) => useMetricPreview({ scope, ...options }), {
        data: draft,
      });
      await waitFor(() => expect(first).toBeDefined());
      if (reason === "unmount") hook.unmount();
      else if (reason === "disabled")
        hook.rerender({ data: draft, enabled: false });
      else
        hook.rerender({
          data: { ...draft, window_start: "2026-11-02T00:00:00Z" },
        });
      await waitFor(() =>
        expect(
          adapter.mock.calls.some(
            ([config]) => config.url === paths.cancelDraftPreview,
          ),
        ).toBe(true),
      );
      expect(first.signal.aborted).toBe(true);
      const cancelConfig = adapter.mock.calls.find(
        ([config]) => config.url === paths.cancelDraftPreview,
      )[0];
      expect(bodyOf(cancelConfig).request_id).toBe(bodyOf(first).request_id);
      await act(async () => {
        pending.resolve(response(first, preview));
      });
      if (reason === "disabled")
        expect(hook.result.current.data).toBeUndefined();
    },
  );

  it.each([
    ["create", useCreateMetric, { scope }, write, definition, 201],
    ["update", useUpdateMetric, args, write, definition, 200],
    [
      "activate",
      useActivateMetric,
      args,
      { version: 1, expected_active_id: null },
      definition,
      200,
    ],
    [
      "refresh",
      useRefreshMetric,
      args,
      { confirm: true },
      { status: "queued" },
      202,
    ],
    [
      "create snapshot",
      useCreateMetricSnapshot,
      args,
      { ...range, idempotency_key: "test-snapshot" },
      {
        id: otherId,
        metric_id: id,
        metric_version: 1,
        ...range,
        as_of: range.window_end,
        filters: [],
        status: "queued",
        kind: "snapshot",
      },
      202,
    ],
    [
      "delete snapshot",
      useDeleteMetricSnapshot,
      { ...args, snapshotId: otherId },
      undefined,
      { status: "cleanup_pending" },
      202,
    ],
  ])(
    "%s neither reissues nor waits for settled draft and saved previews",
    async (_operation, useWrite, options, data, result, status) => {
      const repeatedPreview = deferred();
      const previewRequests = [];
      adapter.mockImplementation((config) => {
        if ([paths.draftPreview, paths.preview(id)].includes(config.url)) {
          previewRequests.push(config);
          // An accidental invalidation would hold mutateAsync/isPending open.
          if (previewRequests.length > 2)
            return repeatedPreview.promise.then(() =>
              response(config, preview),
            );
          return Promise.resolve(response(config, preview));
        }
        return Promise.resolve(response(config, result, status));
      });
      const hook = mount(() => ({
        draft: useMetricPreview({ scope, data: draft }),
        saved: useMetricPreview({ ...args, data: { ...range, version: 1 } }),
        write: useWrite(options),
      }));
      await waitFor(() => {
        expect(hook.result.current.draft.readState).toBe("ready");
        expect(hook.result.current.saved.readState).toBe("ready");
      });
      expect(previewRequests.map(({ url }) => url)).toEqual([
        paths.draftPreview,
        paths.preview(id),
      ]);
      let mutation;
      let completed = false;
      act(() => {
        mutation = hook.result.current.write.mutateAsync(data).then((value) => {
          completed = true;
          return value;
        });
      });
      try {
        await waitFor(() => {
          expect(completed).toBe(true);
          expect(hook.result.current.write.isPending).toBe(false);
        });
        expect(await mutation).toEqual(result);
        expect(previewRequests).toHaveLength(2);
        expect(adapter).toHaveBeenCalledTimes(3);
        for (const read of [
          hook.result.current.draft,
          hook.result.current.saved,
        ]) {
          expect(read.readState).toBe("ready");
          expect(read.isFetching).toBe(false);
          expect(read.data).toEqual(preview);
        }
      } finally {
        // Release a regressed preview too, so failure cannot leak pending work.
        await act(async () => {
          repeatedPreview.resolve();
          await mutation;
        });
      }
    },
  );

  it("invalidates list/catalog and only the created metric's saved reads", async () => {
    serve(definition, 201);
    const hook = mount(() => useCreateMetric({ scope }));
    const savedReads = {
      detail: definition,
      kpi,
      trend: { ...pinned, results: [] },
      drivers,
      history: page,
      fields: { results: [] },
      unitValues: drivers,
      snapshots: page,
    };
    const cached = [];
    const seed = (resource, options, data, invalidated) => {
      const key = [...metricKeys.read(resource, options), true];
      // These cache-only queries must survive the asynchronous mutation.
      hook.client.setQueryDefaults(key, { gcTime: Infinity });
      hook.client.setQueryData(key, data);
      cached.push({ key, invalidated });
    };
    for (const [resource, data] of Object.entries(savedReads)) {
      seed(resource, args, data, true);
      seed(resource, { ...args, version: 2 }, data, true);
      seed(resource, { ...args, metricId: otherId }, data, false);
      seed(
        resource,
        { ...args, scope: { ...scope, workspaceId: otherId } },
        data,
        false,
      );
      seed(
        resource,
        { ...args, scope: { ...scope, organizationId: otherId } },
        data,
        false,
      );
    }
    const catalog = {
      status: true,
      result: {
        metrics: [],
        next_cursor: null,
        total: null,
        total_is_exact: false,
      },
    };
    for (const selected of [scope, { ...scope, projectId: undefined }]) {
      seed("list", { scope: selected }, page, true);
      seed(
        "list",
        { scope: selected, params: { before: otherId } },
        page,
        true,
      );
      seed("catalog", { scope: selected }, catalog, true);
    }
    seed("evaluators", { scope }, { results: [], truncated: false }, false);
    seed(
      "capabilities",
      { scope },
      {
        sources: [],
        levels: ["conversation"],
        kinds: ["count"],
        user_rules: {},
        max_group_dims: 2,
        preview_max_days: 7,
        preview_budget_seconds: 15,
      },
      false,
    );
    seed("preview", { scope, data: draft }, preview, false);
    seed(
      "preview",
      { ...args, data: { ...range, version: 1 } },
      preview,
      false,
    );
    await act(async () => {
      await hook.result.current.mutateAsync(write);
    });
    for (const { key, invalidated } of cached) {
      expect(
        hook.client.getQueryState(key).isInvalidated,
        JSON.stringify(key),
      ).toBe(invalidated);
    }
    expect(adapter).toHaveBeenCalledTimes(1);
  });

  it("invalidates the original workspace and metric after a late mutation, never inventing refresh data", async () => {
    const pending = deferred();
    let config;
    adapter.mockImplementation((request) => {
      config = request;
      return pending.promise;
    });
    const hook = mount((options) => useRefreshMetric(options), args);
    const changed = {
      scope: { ...scope, workspaceId: otherId, projectId: otherId },
      metricId: otherId,
    };
    const detailKeys = [args, { ...args, metricId: otherId }, changed].map(
      (options) => [...metricKeys.read("detail", options), true],
    );
    detailKeys.forEach((key) => {
      hook.client.setQueryDefaults(key, { gcTime: Infinity });
      hook.client.setQueryData(key, definition);
    });
    const invalidate = vi.spyOn(hook.client, "invalidateQueries");
    let promise;
    act(() => {
      promise = hook.result.current.mutateAsync({ confirm: true });
    });
    await waitFor(() => expect(config).toBeDefined());
    hook.rerender(changed);
    await act(async () => {
      pending.resolve(response(config, { status: "already_queued" }, 202));
      await promise;
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: metricKeys.workspace(scope),
      predicate: expect.any(Function),
    });
    expect(
      detailKeys.map((key) => hook.client.getQueryState(key).isInvalidated),
    ).toEqual([true, false, false]);
    expect(
      hook.client.getQueryData([
        ...metricKeys.read("kpi", { ...args, params: range }),
        true,
      ]),
    ).toBeUndefined();
  });

  it.each([
    { scope: { ...scope, projectId: otherId } },
    { version: 2 },
    { snapshotId: otherId },
  ])(
    "does not retain mutation results across identity changes %o",
    async (change) => {
      serve({ status: "queued" }, 202);
      const initial = { ...args, version: 1, snapshotId: id };
      const hook = mount((options) => useRefreshMetric(options), initial);
      await act(async () => {
        await hook.result.current.mutateAsync({ confirm: true });
      });
      await waitFor(() =>
        expect(hook.result.current.data).toEqual({ status: "queued" }),
      );
      hook.rerender({ ...initial, ...change });
      await waitFor(() => expect(hook.result.current.status).toBe("idle"));
      expect(hook.result.current.data).toBeUndefined();
      expect(adapter).toHaveBeenCalledTimes(1);
    },
  );

  it("validation does not invalidate saved metrics and failed writes do not populate cache", async () => {
    serve(validation);
    const hook = mount(() => useValidateMetric({ scope }));
    const invalidate = vi.spyOn(hook.client, "invalidateQueries");
    await act(async () => {
      await hook.result.current.mutateAsync(write);
    });
    expect(invalidate).not.toHaveBeenCalled();
    fail(unavailable, 503);
    const create = mount(() => useCreateMetric({ scope }));
    const createInvalidate = vi.spyOn(create.client, "invalidateQueries");
    await act(async () => {
      await expect(
        create.result.current.mutateAsync(write),
      ).rejects.toMatchObject({ statusCode: 503 });
    });
    expect(createInvalidate).not.toHaveBeenCalled();
  });
});
