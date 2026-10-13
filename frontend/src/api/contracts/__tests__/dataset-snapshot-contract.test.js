import { describe, expect, it } from "vitest";

import { DatasetVersionsCreateBody } from "src/generated/api-contracts/api.zod";

import {
  validateContractedRequestConfig,
  validateContractedResponse,
} from "../openapi-contract";
import createdSnapshot from "./fixtures/dataset-snapshot-created.json";

const validateRuntime = (data) =>
  validateContractedRequestConfig({
    url: "/model-hub/datasets/00000000-0000-0000-0000-000000000001/versions/",
    method: "post",
    data,
  }).ok;

it("accepts the actual 201 empty snapshot response with default blank metadata", () => {
  expect(createdSnapshot.result.description).toBe("");
  expect(createdSnapshot.result.source_revision).toBe("");
  expect(
    validateContractedResponse({
      status: 201,
      data: createdSnapshot,
      config: {
        url: `/model-hub/datasets/${createdSnapshot.result.dataset}/versions/`,
        method: "post",
      },
    }).ok,
  ).toBe(true);
});

describe.each([
  [
    "generated Zod",
    (data) => DatasetVersionsCreateBody.safeParse(data).success,
  ],
  ["runtime OpenAPI", validateRuntime],
])("snapshot %s contract", (_name, accepts) => {
  it.each(["text", 42, false, [], { nested: null }])(
    "accepts JSON input %j",
    (input) => {
      expect(
        accepts({
          items: [
            {
              input,
              expected: null,
              metadata: { nullable: null, score: 1 },
              external_id: null,
            },
          ],
          metadata: { tags: ["eval", null] },
          parent_version_id: null,
        }),
      ).toBe(true);
    },
  );

  it.each([
    {},
    { items: [{}] },
    { items: [{ input: null }] },
    { items: [{ input: "ok", created_by: "caller" }] },
    { items: [], organization: "caller" },
    { items: [{ input: "ok", metadata: [] }] },
  ])("rejects invalid or server-owned fields %j", (data) => {
    expect(accepts(data)).toBe(false);
  });

  it("requires the explicit list and enforces its inclusive bound", () => {
    expect(accepts({ items: [] })).toBe(true);
    expect(accepts({ items: Array(10_000).fill({ input: "ok" }) })).toBe(true);
    expect(accepts({ items: Array(10_001).fill({ input: "ok" }) })).toBe(false);
  });
});
