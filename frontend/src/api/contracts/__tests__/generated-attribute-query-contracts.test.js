import { afterEach, describe, expect, it, vi } from "vitest";

// Only URL construction is exercised: never import Axios/auth or send a request.
vi.mock("../openapi-mutator", () => ({
  apiMutator: vi.fn(() => {
    throw new Error("network transport is forbidden");
  }),
}));

import { apiMutator } from "../openapi-mutator";
import {
  getApiTracesSpanAttributeDetailListUrl,
  getApiTracesSpanAttributeKeysListUrl,
  getApiTracesSpanAttributeValuesListUrl,
  getTracerObservationSpanGetSpanAttributesListUrl,
} from "src/generated/api-contracts/api";
import {
  ApiTracesSpanAttributeDetailListQueryParams,
  ApiTracesSpanAttributeKeysListQueryParams,
  ApiTracesSpanAttributeValuesListQueryParams,
  TracerObservationSpanGetSpanAttributesListQueryParams,
} from "src/generated/api-contracts/api.zod";

const PROJECT = "c4de3065-12b5-488c-a814-aa1c8e3f856f";
const ROUTES = [
  {
    path: "/api/traces/span-attribute-detail/",
    field: "key",
    schema: ApiTracesSpanAttributeDetailListQueryParams,
    url: getApiTracesSpanAttributeDetailListUrl,
    defaults: { project_id: PROJECT, refresh: false },
  },
  {
    path: "/api/traces/span-attribute-keys/",
    field: "q",
    schema: ApiTracesSpanAttributeKeysListQueryParams,
    url: getApiTracesSpanAttributeKeysListUrl,
    defaults: {
      project_id: PROJECT,
      workspace_scope: false,
      discovery_mode: "filter",
      page_size: 50,
    },
  },
  {
    path: "/api/traces/span-attribute-values/",
    field: "key",
    schema: ApiTracesSpanAttributeValuesListQueryParams,
    url: getApiTracesSpanAttributeValuesListUrl,
    defaults: { project_id: PROJECT, limit: 50 },
  },
  {
    path: "/tracer/observation-span/get_span_attributes_list/",
    field: "q",
    schema: TracerObservationSpanGetSpanAttributesListQueryParams,
    url: getTracerObservationSpanGetSpanAttributesListUrl,
    defaults: {
      filters: JSON.stringify({ project_id: PROJECT }),
      row_type: "spans",
    },
  },
];
const KEYS = [
  ["ascii-over-old-limit", "a".repeat(513)],
  ["unicode-4096-bytes", "😀".repeat(1024)],
  ["whitespace-only", " \t\n"],
  ["nul", "\0"],
  ["literal-plus", "a+b"],
  ["literal-percent-zero", "%00"],
  ["combined-exact-key", " 客户\t\0+%00\n "],
];

afterEach(() => expect(apiMutator).not.toHaveBeenCalled());

describe.each(ROUTES)(
  "generated exact attribute query $path",
  ({ path, field, schema, url, defaults }) => {
    // Do not infer byte-bound enforcement from Zod string length. The backend's
    // exact field is authoritative; these tests prevent stale client rejection.
    it.each(KEYS)("Zod preserves valid %s", (_label, key) => {
      const params = { ...defaults, [field]: key };
      const before = JSON.stringify(params);
      const parsed = schema.safeParse(params);
      expect(parsed.success).toBe(true);
      expect(parsed.data[field]).toBe(key);
      expect(JSON.stringify(params)).toBe(before);
    });

    it.each(KEYS)(
      "URL helper round-trips %s without transport",
      (_label, key) => {
        const params = { ...defaults, [field]: key };
        const before = JSON.stringify(params);
        const generated = url(params);
        const parsed = new URL(generated, "http://offline.invalid");
        expect(parsed.pathname).toBe(path);
        expect(parsed.searchParams.getAll(field)).toEqual([key]);
        for (const [name, value] of Object.entries(defaults)) {
          expect(parsed.searchParams.get(name)).toBe(String(value));
        }
        expect(JSON.stringify(params)).toBe(before);
        if (key.includes("\0")) expect(generated).toContain("%00");
        if (key.includes("+")) expect(generated).toContain("%2B");
        if (key.includes("%00")) expect(generated).toContain("%2500");
      },
    );
  },
);

it("retains generated values-search, project, page-size and cursor contracts", () => {
  const values = ApiTracesSpanAttributeValuesListQueryParams;
  expect(
    values.safeParse({
      project_id: PROJECT,
      key: "ordinary",
      q: "x".repeat(512),
    }).success,
  ).toBe(true);
  expect(
    values.safeParse({
      project_id: PROJECT,
      key: "ordinary",
      q: "x".repeat(513),
    }).success,
  ).toBe(false);
  for (const schema of [values, ApiTracesSpanAttributeDetailListQueryParams]) {
    expect(schema.safeParse({ key: "ordinary" }).success).toBe(false);
    expect(
      schema.safeParse({ project_id: "not-a-uuid", key: "ordinary" }).success,
    ).toBe(false);
    expect(schema.safeParse({ project_id: PROJECT }).success).toBe(false);
  }
  const keys = ApiTracesSpanAttributeKeysListQueryParams;
  expect(
    keys.safeParse({
      project_id: PROJECT,
      page_size: 50,
      cursor: "x".repeat(8192),
    }).success,
  ).toBe(true);
  expect(
    keys.safeParse({
      project_id: PROJECT,
      page_size: 50,
      cursor: "x".repeat(8193),
    }).success,
  ).toBe(false);
  expect(keys.safeParse({ project_id: PROJECT, page_size: 51 }).success).toBe(
    false,
  );
  expect(keys.safeParse({ project_id: "not-a-uuid" }).success).toBe(false);
  expect(
    TracerObservationSpanGetSpanAttributesListQueryParams.safeParse({
      q: "ordinary",
    }).success,
  ).toBe(false);
});
