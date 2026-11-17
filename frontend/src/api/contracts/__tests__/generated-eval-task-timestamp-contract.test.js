import { describe, expect, it } from "vitest";

import {
  TracerEvalTaskCreateBody,
  TracerEvalTaskPartialUpdateBody,
  TracerEvalTaskReadResponse,
  TracerEvalTaskUpdateBody,
} from "src/generated/api-contracts/api.zod";

const timestamps = ["start_time", "end_time"];

describe("generated evaluation task lifecycle timestamps", () => {
  it.each([
    ["create", TracerEvalTaskCreateBody],
    ["update", TracerEvalTaskUpdateBody],
    ["partial update", TracerEvalTaskPartialUpdateBody],
  ])("does not advertise lifecycle timestamps in %s inputs", (_, schema) => {
    for (const field of timestamps) {
      expect(schema.shape).not.toHaveProperty(field);
    }
  });

  it.each(timestamps)("preserves %s in responses", (field) => {
    const schema = TracerEvalTaskReadResponse.shape[field];
    expect(schema.parse("2026-01-01T00:00:00Z")).toBe("2026-01-01T00:00:00Z");
  });
});
