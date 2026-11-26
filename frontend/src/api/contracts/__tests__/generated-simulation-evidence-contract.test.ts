import { expect, it } from "vitest";

import { tool } from "./simulation-evidence-tool.fixture";
import { TracerInternalErrorFeedV2AttemptsSimulationEvidenceCreateResponse as Response } from "../../../generated/api-contracts/api.zod";

const call = Response.shape.calls.element.shape;

it("allows recorded JSON values in tool evidence", () => {
  expect(call.tool_calls.parse([tool])).toEqual([tool]);
});

it("accepts only JSON objects or null for persona", () => {
  const nested = { traits: ["patient", null, { active: true }] };
  expect(call.persona.parse(nested)).toEqual(nested);
  expect(call.persona.parse(null)).toBeNull();
  for (const invalid of [false, [], undefined]) {
    expect(call.persona.safeParse(invalid).success).toBe(false);
  }
});
