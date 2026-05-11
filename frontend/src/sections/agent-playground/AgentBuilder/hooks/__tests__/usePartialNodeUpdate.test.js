import { describe, expect, it } from "vitest";
import { buildEvaluationPatchPayload } from "../usePartialNodeUpdate";

describe("buildEvaluationPatchPayload", () => {
  it("serializes evaluation config with backend field names and output ports", () => {
    const patch = buildEvaluationPatchPayload({
      label: "quality_gate",
      evaluators: [{ templateId: "eval-template-1" }],
      config: {
        threshold: 0.75,
        failAction: "route_fallback",
        payload: {
          ports: [
            { key: "input", direction: "input" },
            { key: "evaluation_result", direction: "output" },
            { key: "passthrough", direction: "output" },
          ],
        },
      },
    });

    expect(patch).toEqual({
      name: "quality_gate",
      config: {
        evaluators: [{ templateId: "eval-template-1" }],
        threshold: 0.75,
        fail_action: "route_fallback",
      },
      ports: [
        { key: "evaluation_result", direction: "output" },
        { key: "passthrough", direction: "output" },
      ],
    });
  });
});
