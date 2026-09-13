import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readQuery, default as client } from "src/utils/axios";
import { getContractedApiMethods } from "src/api/contracts/api-surface";
import { findOpenApiEndpoint } from "src/api/contracts/openapi-contract";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";
import { serializeFilterListForApi } from "src/api/contracts/filter-contract";

vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), error: vi.fn() },
}));
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
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

const pid = "00000000-0000-0000-0000-00000000000b";
const version = "00000000-0000-0000-0000-00000000000c";
const row = "00000000-0000-0000-0000-00000000000d";
const routes = [
  ["trace/list_traces_of_session/", { project_id: pid }],
  ["observation-span/list_spans_observe/", { project_id: pid }],
  ["trace-session/list_sessions/", { project_id: pid }],
  ["users/", { project_id: pid }],
  ["trace/list_traces/", { project_version_id: version }],
  ["observation-span/list_spans/", { project_version_id: version }],
  ["trace/list_voice_calls/", { project_id: pid }],
  [
    "trace/get_trace_id_by_index/",
    { project_version_id: version, trace_id: row },
  ],
  ["trace/get_trace_id_by_index_observe/", { project_id: pid, trace_id: row }],
  [
    "observation-span/get_trace_id_by_index_spans_as_base/",
    { project_version_id: version, span_id: row },
  ],
  [
    "observation-span/get_trace_id_by_index_spans_as_observe/",
    { project_id: pid, span_id: row },
  ],
  ["trace/agent_graph/", { project_id: pid }],
];
const leaf = (
  col_type,
  column_id,
  filter_type,
  filter_op,
  filter_value,
  types,
) => ({
  column_id,
  filter_config: {
    col_type,
    filter_type,
    filter_op,
    filter_value,
    ...(types && { attribute_value_types: types }),
  },
});
const filters = JSON.stringify(
  serializeFilterListForApi([
    leaf("SPAN_ATTRIBUTE", "attempt", "number", "between", [0, 10]),
    leaf("SPAN_ATTRIBUTE", "attempt", "number", "between", [5, 20]),
    leaf(
      "SPAN_ATTRIBUTE",
      "mixed",
      "text",
      "in",
      ["0", 0, false],
      ["string", "number", "boolean"],
    ),
    leaf("SPAN_ATTRIBUTE", "flag", "boolean", "equals", false),
    leaf("ANNOTATION", "review", "categorical", "in", [
      "needs,review",
      " approved ",
    ]),
    leaf("EVAL_METRIC", "quality", "categorical", "in", [
      "pass,with-note",
      " pass ",
    ]),
    leaf("SPAN_ATTRIBUTE", "missing", "text", "is_null", null),
  ]),
);
const adapter = vi.fn(async () => ({
  status: 200,
  data: { fixture: true },
  headers: {},
}));
// Deliberately no server-response contract or successful query-population claim.
const originalAdapter = client.defaults.adapter;
beforeEach(() => {
  adapter.mockClear();
  client.defaults.adapter = adapter;
});
afterEach(() => {
  client.defaults.adapter = originalAdapter;
  vi.restoreAllMocks();
});

describe("opt-in query read transport", () => {
  it("declares exactly thirteen enabled POST reads, not a generic POST-to-list rule", () => {
    expect(
      Object.values(OPENAPI_CONTRACT.endpoints).filter(
        (op) => op.post?.readQueryPost,
      ),
    ).toHaveLength(13);
    expect(readQuery).toBeTypeOf("function");
  });
  it("keeps large session navigation filters entirely in the POST body", async () => {
    const navigation = {
      workspace_id: pid,
      project_id: pid,
      filters: Array.from({ length: 10 }, (_, i) =>
        leaf(
          "SPAN_ATTRIBUTE",
          `text_${i}`,
          "text",
          "contains",
          "x".repeat(16384),
        ),
      ),
      sort_params: [],
    };
    const url = `/tracer/trace-session/${pid}/query/`;
    const params = {
      navigation_context: JSON.stringify(navigation),
      page_number: 0,
      page_size: 10,
    };
    await readQuery(url, { params });
    const config = adapter.mock.calls.at(-1)[0];
    expect(config.method).toBe("post");
    expect(config.url).toBe(url);
    expect(config.params).toBeUndefined();
    expect(JSON.parse(config.data)).toEqual(params);
    expect(config.data.length).toBeGreaterThan(160_000);
  });
  it.each(routes)(
    "preserves the actual Axios wire on %s",
    async (route, scope) => {
      const url = `/tracer/${route}`;
      const params = { ...scope, filters };
      const before = JSON.stringify(params);
      await readQuery(url, { params });
      const config = adapter.mock.calls.at(-1)[0];
      expect(config.method).toBe("post");
      expect(config.params).toBeUndefined();
      expect(config.url).toBe(url);
      expect(JSON.parse(config.data)).toEqual(params);
      expect(JSON.stringify(params)).toBe(before);
      expect(getContractedApiMethods(url)).toContain("get");
      expect(getContractedApiMethods(url)).toContain("post");
      expect(
        findOpenApiEndpoint(url, "post").contract.runtimeRequestValidation,
      ).toBe(true);
    },
  );
  it("retains embedded project, opaque typed JSON, omitted nulls, scope headers and cancellation signal", async () => {
    const signal = new AbortController().signal;
    const headers = {
      Authorization: "Bearer offline",
      "X-Organization-Id": pid,
      "X-Workspace-Id": version,
    };
    const encoded = JSON.stringify([
      { value: null, values: ["0", 0, false, ' exact\\"text '] },
    ]);
    const params = {
      filters: encoded,
      search: null,
      requested_columns: "[]",
      attribute_keys: '["exact,comma.key"]',
      cursor: "opaque-token",
      cursor_mode: true,
      page_size: 10,
    };
    await readQuery(`/tracer/users/?project_id=${pid}`, {
      params,
      headers,
      signal,
      timeout: 1234,
    });
    const config = adapter.mock.calls.at(-1)[0];
    expect(config.url).toBe("/tracer/users/");
    expect(config.signal).toBe(signal);
    expect(config.timeout).toBe(1234);
    expect(config.headers.toJSON()).toMatchObject(headers);
    expect(JSON.parse(config.data)).toEqual({
      ...params,
      project_id: pid,
      search: undefined,
    });
  });
  it.each(["?project_id=one", "?project_id=one&project_id=two"])(
    "rejects duplicate scope without I/O: %s",
    (query) => {
      expect(() =>
        readQuery(`/tracer/users/${query}`, { params: { project_id: pid } }),
      ).toThrow("Duplicate");
      expect(adapter).not.toHaveBeenCalled();
    },
  );
  it("rejects an independently supplied body", () => {
    expect(() => readQuery("/tracer/users/", { data: {}, params: {} })).toThrow(
      "owned by params",
    );
    expect(adapter).not.toHaveBeenCalled();
  });
  it("does not use model-create POST for an unmarked read", async () => {
    await readQuery("/tracer/trace/", { params: { project_id: pid } });
    expect(adapter.mock.calls.at(-1)[0].method).toBe("get");
    expect(adapter.mock.calls.at(-1)[0].params).toEqual({ project_id: pid });
  });
  it("keeps direct GET backward compatible", async () => {
    await client.get("/tracer/users/", {
      params: { project_id: pid, filters },
    });
    const config = adapter.mock.calls.at(-1)[0];
    expect(config.method).toBe("get");
    expect(config.params.filters).toBe(filters);
  });
  it("validates generated POST bodies instead of silently skipping method validation", async () => {
    const validate = client.interceptors.request.handlers.find(
      (handler) => handler.fulfilled,
    ).fulfilled;
    expect(() =>
      validate({
        url: "/tracer/users/",
        method: "post",
        data: { page_size: "not-an-integer" },
      }),
    ).toThrow("request body contract validation failed");
    await expect(
      readQuery("/tracer/users/", { params: { page_size: "not-an-integer" } }),
    ).rejects.toHaveProperty("message");
    expect(adapter).not.toHaveBeenCalled();
  });
  it("does not send an already-cancelled request", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      readQuery("/tracer/users/", { signal: controller.signal }),
    ).rejects.toHaveProperty("transportCode", "ERR_CANCELED");
    expect(adapter).not.toHaveBeenCalled();
  });
  it("preserves pending cancellation and lets the replacement finish", async () => {
    let finish;
    const started = new Promise((resolve) =>
      adapter.mockImplementationOnce((config) => {
        resolve(config);
        return new Promise((done) => {
          finish = done;
        });
      }),
    );
    const controller = new AbortController();
    const pending = readQuery("/tracer/users/", { signal: controller.signal });
    const cancelled = expect(pending).rejects.toHaveProperty(
      "transportCode",
      "ERR_CANCELED",
    );
    expect((await started).signal).toBe(controller.signal);
    controller.abort();
    finish({ status: 200, data: { fixture: true }, headers: {} });
    await cancelled;
    await expect(readQuery("/tracer/users/")).resolves.toHaveProperty(
      "status",
      200,
    );
    expect(adapter).toHaveBeenCalledTimes(2);
  });
});
