import { describe, it, expect } from "vitest";
import {
  buildAgentEvaluationColumns,
  getEvaluatorId,
  hasEvaluatorMappings,
} from "../../../utils/evaluationUtils";

describe("buildAgentEvaluationColumns", () => {
  it("exposes every graph node output as a mapping target", () => {
    const columns = buildAgentEvaluationColumns({
      nodes: [
        {
          id: "node-1",
          name: "Retriever",
          ports: [
            { key: "query", direction: "input" },
            { key: "docs", displayName: "docs", direction: "output" },
          ],
        },
        {
          id: "node-2",
          name: "Answer",
          ports: [
            { key: "response", display_name: "response", direction: "output" },
          ],
        },
      ],
    });

    expect(columns).toEqual([
      {
        field: "node-1.docs",
        headerName: "Retriever.docs",
        dataType: "text",
      },
      {
        field: "node-2.response",
        headerName: "Answer.response",
        dataType: "text",
      },
    ]);
  });

  it("requires mappings on every selected evaluator before running", () => {
    expect(
      hasEvaluatorMappings([
        { mapping: { output: "node-1.docs" } },
        { config: { mapping: { output: "node-2.response" } } },
      ]),
    ).toBe(true);

    expect(
      hasEvaluatorMappings([
        { mapping: { output: "node-1.docs" } },
        { config: {} },
      ]),
    ).toBe(false);
  });
});

describe("getEvaluatorId", () => {
  it("normalizes evaluator identifiers from dialog, persisted, and API shapes", () => {
    expect(getEvaluatorId({ evalId: "runtime-id" })).toBe("runtime-id");
    expect(getEvaluatorId({ eval_id: "legacy-id" })).toBe("legacy-id");
    expect(getEvaluatorId({ templateId: "dialog-template" })).toBe(
      "dialog-template",
    );
    expect(getEvaluatorId({ template_id: "api-template" })).toBe(
      "api-template",
    );
    expect(getEvaluatorId({ id: "fallback-id" })).toBe("fallback-id");
  });
});
