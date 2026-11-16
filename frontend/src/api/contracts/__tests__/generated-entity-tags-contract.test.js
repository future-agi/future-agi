import { describe, expect, it } from "vitest";
import {
  validateContractedRequestConfig,
  validateContractedResponse,
} from "../openapi-contract";
import {
  TracerTraceUpdateTagsBody,
  TracerObservationSpanUpdateTagsBody,
} from "src/generated/api-contracts/api.zod";

const target = {
  project_id: "00000000-0000-4000-8000-000000000001",
  trace_id: "trace-1",
  span_id: "root-1",
  start_hour: "2026-11-08T10:00:00+00:00",
  observation_type: "",
  service_name: "",
  expected_start_time: "2026-11-08T10:15:20.123456+00:00",
  expected_version: "18446744073709551614",
  expected_tag_revision: 0,
};
const endpoints = [
  {
    url: "/tracer/trace/trace-1/tags/",
    method: "patch",
    schema: TracerTraceUpdateTagsBody,
  },
  {
    url: "/tracer/observation-span/update-tags/",
    method: "post",
    schema: TracerObservationSpanUpdateTagsBody,
  },
];

describe.each(endpoints)(
  "scoped tag contract: $method $url",
  ({ url, method, schema }) => {
    it.each([["sdk-name"], [{ name: "reviewed", color: "#123456" }], []])(
      "accepts legacy and rich tag items, including clear (%j)",
      (...tags) => {
        const body = { ...target, tags };
        expect(
          validateContractedRequestConfig({ url, method, data: body }).ok,
        ).toBe(true);
        expect(schema.safeParse(body).success).toBe(true);
      },
    );

    it("rejects ID-only writes and retains timestamp/version precision", () => {
      expect(
        validateContractedRequestConfig({ url, method, data: { tags: [] } }).ok,
      ).toBe(false);
      expect(schema.safeParse({ tags: [] }).success).toBe(false);
      expect(schema.parse({ ...target, tags: [] })).toEqual({
        ...target,
        tags: [],
      });
    });

    it("requires rich tag names and generates the color constraint", () => {
      expect(
        validateContractedRequestConfig({
          url,
          method,
          data: { ...target, tags: [{}] },
        }).ok,
      ).toBe(false);
      for (const tags of [[{}], [{ name: "reviewed", color: "red" }]]) {
        expect(schema.safeParse({ ...target, tags }).success).toBe(false);
      }
    });

    it.each([404, 409, 503])("declares the %s failure response", (status) => {
      expect(
        validateContractedResponse({
          config: { url, method },
          status,
          data: {
            status: false,
            code: "tag_target_changed",
            detail: "Refetch before saving.",
          },
        }).ok,
      ).toBe(true);
    });
  },
);
