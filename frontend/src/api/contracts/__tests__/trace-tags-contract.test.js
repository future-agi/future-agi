import { describe, expect, it } from "vitest";
import {
  handleApiContractValidation,
  validateContractedRequestConfig,
} from "../openapi-contract";

const TRACE = "11111111-1111-4111-8111-111111111111";
const request = (data) => ({
  url: `/tracer/trace/${TRACE}/tags/`,
  method: "patch",
  data,
});

const strictly = (result) => () =>
  handleApiContractValidation(result, { strict: true });

const expectTagEndpoint = (result) => {
  expect(result.skipped).toBeUndefined();
  if (result.ok) {
    expect(result.endpoint).toMatchObject({
      template: "/tracer/trace/{id}/tags/",
      method: "patch",
      contract: { runtimeRequestValidation: true },
    });
  } else {
    expect(result.error.details).toMatchObject({
      kind: "request body",
      endpoint: "/tracer/trace/{id}/tags/",
      method: "patch",
      schemaName: "TraceTagsUpdate",
    });
  }
};

describe("trace tags request contract", () => {
  it.each([
    ["UI name/color tags", [{ name: "reviewed", color: "#7857FC" }]],
    [
      "object-only tags with optional color",
      [{ name: "reviewed" }, { name: "follow-up", color: "" }],
    ],
    ["an explicit clear", []],
  ])("accepts %s in strict mode", (_label, tags) => {
    const result = validateContractedRequestConfig(request({ tags }));
    expectTagEndpoint(result);
    expect(result.ok).toBe(true);
    expect(strictly(result)).not.toThrow();
  });

  // Plain strings remain a backend-only legacy format; the UI sends objects.
  it.each([
    ["an empty name", [{ name: "" }]],
    ["a missing name", [{ color: "#7857FC" }]],
    ["a numeric item", [42]],
    ["a plain string item", ["reviewed"]],
    ["mixed legacy and object tags", ["reviewed", { name: "follow-up" }]],
    ["a non-string color", [{ name: "reviewed", color: 42 }]],
    ["a null item", [null]],
  ])("rejects %s in strict mode", (_label, tags) => {
    const result = validateContractedRequestConfig(request({ tags }));
    expectTagEndpoint(result);
    expect(result.ok).toBe(false);
    expect(strictly(result)).toThrow(/request body contract validation failed/);
  });

  it("rejects a missing tags field in strict mode", () => {
    const result = validateContractedRequestConfig(request({}));
    expectTagEndpoint(result);
    expect(result.ok).toBe(false);
    expect(strictly(result)).toThrow(/request body contract validation failed/);
  });
});
