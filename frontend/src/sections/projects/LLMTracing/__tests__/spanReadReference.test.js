import { describe, expect, it } from "vitest";
import {
  canonicalSpanTimestamp,
  getSpanReadReference,
  getSpanReadIdentityKey,
  getSpanReadCacheKey,
  spanReadRequest,
  verifySpanReadResponse,
} from "../spanReadReference";

const row = {
  project_id: "project-a",
  trace_id: "trace-a",
  span_id: "same-span",
  start_time: "2026-09-05T10:15:30.123456+05:30",
  observation_type: "SPAN",
  service_name: "svc",
  _version: "18446744073709551615",
};

describe("read-only physical span references", () => {
  it("keeps offset-aware microseconds and UInt64 versions without Number rounding", () => {
    expect(getSpanReadReference(row)).toMatchObject({
      start_hour: "2026-09-05T04:00:00.000000Z",
      expected_start_time: "2026-09-05T04:45:30.123456Z",
      expected_version: "18446744073709551615",
    });
    expect(spanReadRequest(row)).toEqual({
      spanId: row.span_id,
      params: {
        project_id: row.project_id,
        trace_id: row.trace_id,
        start_hour: "2026-09-05T04:00:00.000000Z",
        observation_type: "SPAN",
        service_name: "svc",
        expected_start_time: "2026-09-05T04:45:30.123456Z",
        expected_version: "18446744073709551615",
      },
    });
  });

  it.each([
    ["project_id", "project-b"],
    ["trace_id", "trace-b"],
    ["span_id", "other-span"],
    ["start_time", "2026-09-05T05:15:00Z"],
    ["observation_type", "GENERATION"],
    ["service_name", "other"],
  ])("distinguishes the physical %s discriminator", (field, value) => {
    const other = { ...row, [field]: value };
    expect(getSpanReadIdentityKey(other)).not.toBe(getSpanReadIdentityKey(row));
    expect(() => verifySpanReadResponse(row, other)).toThrow();
  });

  it("keeps one physical key but invalidates a corrected timestamp/version cache", () => {
    const corrected = {
      ...row,
      start_time: "2026-09-05T04:45:30.123457Z",
      _version: "18446744073709551614",
    };
    expect(getSpanReadIdentityKey(corrected)).toBe(getSpanReadIdentityKey(row));
    expect(getSpanReadCacheKey(corrected)).not.toBe(getSpanReadCacheKey(row));
    expect(() => verifySpanReadResponse(row, corrected)).toThrow();
  });

  it("accepts the additive detail aliases and blank physical service/type", () => {
    const detail = {
      ...row,
      project: row.project_id,
      trace: row.trace_id,
      id: row.span_id,
    };
    delete detail.project_id;
    delete detail.trace_id;
    delete detail.span_id;
    expect(verifySpanReadResponse(row, detail)).toBe(detail);
    expect(
      getSpanReadReference({ ...row, service_name: "", observation_type: "" }),
    ).not.toBeNull();
    expect(getSpanReadReference({ ...row, project: "foreign" })).toBeNull();
  });

  it.each([
    undefined,
    null,
    17,
    9007199254740992,
    "-1",
    "1.0",
    "+1",
    "00",
    "18446744073709551616",
  ])("rejects an unverified version %s", (_version) => {
    expect(getSpanReadReference({ ...row, _version })).toBeNull();
  });

  it.each([
    "project_id",
    "trace_id",
    "span_id",
    "start_time",
    "service_name",
    "observation_type",
    "_version",
  ])("does not fabricate a missing %s", (field) => {
    const invalid = { ...row };
    delete invalid[field];
    expect(() => spanReadRequest(invalid)).toThrow();
    expect(() => verifySpanReadResponse(row, invalid)).toThrow();
  });

  it.each([
    "2026-02-30T00:00:00Z",
    "2025-02-29T00:00:00Z",
    "2026-09-05T24:00:00Z",
    "2026-09-05T00:00:00",
    "2026-09-05",
    "2026-09-05T00:00:00.1234567Z",
    "2026-09-05T00:00:00+24:00",
  ])("rejects a malformed or lossy timestamp %s", (value) => {
    expect(canonicalSpanTimestamp(value)).toBeNull();
  });

  it("normalizes equivalent timestamps and rejects inconsistent explicit hours", () => {
    expect(canonicalSpanTimestamp("2024-02-29T00:00:00Z")).toBe(
      "2024-02-29T00:00:00.000000Z",
    );
    expect(
      getSpanReadCacheKey({
        ...row,
        start_time: "2026-09-05T04:45:30.123456Z",
      }),
    ).toBe(getSpanReadCacheKey(row));
    expect(
      getSpanReadReference({ ...row, start_hour: "2026-09-05T05:00:00Z" }),
    ).toBeNull();
  });
});
