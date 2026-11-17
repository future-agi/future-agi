import { describe, expect, it } from "vitest";
import { buildLogsDrilldown } from "./logsDrilldown";

const range = {
  start: "2026-11-09T12:34:56.123+05:30",
  end: "2026-11-09T13:34:56.456+05:30",
  gatewayId: "gw/one & two",
};
const params = (link) =>
  Object.fromEntries(new URL(link.href, "https://console.test").searchParams);
const windowParams = {
  started_after: range.start,
  started_before: range.end,
  gateway_id: range.gatewayId,
};

describe("Analytics request links", () => {
  it("preserves exact instants, gateway, and status 429 with errors only", () => {
    const link = buildLogsDrilldown({
      ...range,
      dimensions: { status_code: 429 },
      isError: true,
    });
    expect(link.scoped).toBe(true);
    expect(params(link)).toEqual({
      ...windowParams,
      status_code: "429",
      is_error: "true",
    });
  });

  it.each([
    "model",
    "provider",
    "application",
    "service",
    "api_key_id",
    "user_id",
  ])(
    "encodes the exact %s without interpreting punctuation or Unicode",
    (dimension) => {
      const value = "team/模型 & name+v2?mode=#1";
      const link = buildLogsDrilldown({
        ...range,
        dimensions: { [dimension]: value },
      });
      expect(link.scoped).toBe(true);
      expect(params(link)).toEqual({ ...windowParams, [dimension]: value });
      expect(link.href).not.toContain("模型");
    },
  );

  it.each([
    ["Future AGI credits", "managed"],
    ["Own key", "byok"],
    ["managed", "managed"],
    ["byok", "byok"],
  ])("maps the billing group %s to the supported raw value", (label, mode) => {
    expect(
      params(
        buildLogsDrilldown({ ...range, dimensions: { billing_mode: label } }),
      ),
    ).toEqual({ ...windowParams, billing_mode: mode });
  });

  it("preserves a model row's available dimensions, matching the managed provider as Logs does", () => {
    const link = buildLogsDrilldown({
      ...range,
      dimensions: {
        model: "vendor/model+v2",
        provider: "openrouter",
        billing_mode: "managed",
      },
    });
    expect(params(link)).toEqual({
      ...windowParams,
      model: "vendor/model+v2",
      provider: "Future AGI",
      billing_mode: "managed",
    });
  });

  it.each(["Other", "Unknown", "unknown", "", null, "comma,value", " padded "])(
    "offers only the range for a group that cannot be filtered exactly: %s",
    (name) => {
      const link = buildLogsDrilldown({
        ...range,
        dimensions: { application: name },
        isError: true,
      });
      expect(link.scoped).toBe(false);
      expect(params(link)).toEqual({ ...windowParams, is_error: "true" });
    },
  );

  it.each(["api_key_id", "user_id"])(
    "keeps exact %s identifiers including commas, but not synthetic groups",
    (dimension) => {
      expect(
        params(
          buildLogsDrilldown({
            ...range,
            dimensions: { [dimension]: "tenant,user" },
          }),
        ),
      ).toEqual({ ...windowParams, [dimension]: "tenant,user" });
      for (const value of ["Other", "unknown", "", null]) {
        expect(
          buildLogsDrilldown({ ...range, dimensions: { [dimension]: value } })
            .scoped,
        ).toBe(false);
      }
    },
  );

  it("never fabricates an unsupported dimension or status", () => {
    for (const dimensions of [
      { custom_tag: "value" },
      { status_code: "invalid" },
    ]) {
      const link = buildLogsDrilldown({ ...range, dimensions });
      expect(link.scoped).toBe(false);
      expect(params(link)).toEqual(windowParams);
    }
  });

  it("does not offer a misleading unbounded link when a range is missing", () => {
    expect(
      buildLogsDrilldown({ start: range.start, dimensions: { model: "m" } })
        .href,
    ).toBeNull();
    expect(buildLogsDrilldown({ end: range.end }).href).toBeNull();
  });
});
