// A New experiment with no run setting changed names none of them in its
// create body. Turning run settings on then moves no score: a failed row keeps
// the server's default (no score, out of every average), and a server with run
// settings turned off still accepts the create. The Review step says that
// default, and names Count as 0 only as the option it is.
import { describe, expect, it } from "vitest";
import { experimentBodyOfValues } from "../experimentBody";
import { emptyExperimentValues } from "../experimentFormValues";
import { runScopeBody, SCOPE_FIELDS } from "../runScopeBody";
import { errorPolicyLine } from "../scopeReceipt";
import { wireExperimentBody } from "../serverBody";

const DATASET = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const Q = { id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", name: "question" };

const plain = (over = {}) => ({
  ...emptyExperimentValues(),
  name: "Plain",
  promptConfig: [
    {
      experimentType: "llm",
      targetType: "prompt",
      promptId: "1b2c3d4e-0000-4000-8000-000000000001",
      promptVersion: "1b2c3d4e-0000-4000-8000-000000000002",
      model: [{ value: "gpt-4o-mini", providers: "openai" }],
      modelParams: {},
      configuration: { toolChoice: "auto", tools: [] },
      variables: ["question"],
      variableMapping: { question: Q.id },
    },
  ],
  ...over,
});

const bodyOf = (values) =>
  experimentBodyOfValues(values, { datasetId: DATASET });

describe("a New experiment with no run setting changed", () => {
  it("names no run setting in the body it builds or sends", () => {
    const body = bodyOf(plain());
    const scoped = runScopeBody(body);
    const wire = wireExperimentBody(body, { columns: [Q] }, false);
    expect(wire.blocks).toEqual([]);
    SCOPE_FIELDS.forEach((field) => {
      expect(scoped, field).not.toHaveProperty(field);
      expect(wire.body, field).not.toHaveProperty(field);
    });
  });

  it("sends an error policy only when the person chose one", () => {
    ["zero", "exclude"].forEach((errorPolicy) => {
      expect(runScopeBody(bodyOf(plain({ errorPolicy }))).error_policy).toBe(
        errorPolicy,
      );
    });
    expect(
      runScopeBody(bodyOf(plain({ errorPolicy: null }))),
    ).not.toHaveProperty("error_policy");
  });

  it("says the default the server applies, and Count as 0 only when chosen", () => {
    expect(errorPolicyLine(undefined)).toBe(
      "Errors get no score and stay out of averages (default)",
    );
    expect(errorPolicyLine("zero")).toBe("Errors count as 0 in scores");
    expect(errorPolicyLine("exclude")).toBe("Errors excluded from scores");
  });
});
