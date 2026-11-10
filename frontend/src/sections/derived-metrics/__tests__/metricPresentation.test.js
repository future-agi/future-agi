import { describe, expect, it } from "vitest";
import {
  definitionSummary,
  enumLabel,
  inputLabel,
  inputSourceKind,
  recipeSentence,
  REDUCER_LABELS,
  unitInputs,
  VALUE_FORMAT_LABELS,
  VALUE_TYPE_LABELS,
  WEIGHTING_LABELS,
} from "../metricPresentation";

const resolved = {
  alias: "value_1",
  source: "span",
  field: "attribute_number",
  attribute_key: "resolved",
  value_type: "number",
  reducer: "latest",
};
const duration = {
  alias: "value_2",
  source: "span",
  field: "latency_ms",
  value_type: "number",
  reducer: "mean",
};

describe("metric presentation", () => {
  it("names an input by its source, falling back to the alias", () => {
    const inputs = [resolved, duration];
    expect(inputLabel("value_1", { inputs })).toBe("resolved");
    expect(inputLabel("value_2", { inputs })).toBe("Duration");
    expect(
      inputLabel("value_2", { inputs, sourceLabels: { value_2: "Latency" } }),
    ).toBe("Latency");
    expect(inputLabel("passed", { inputs })).toBe("passed");
    expect(inputLabel("price")).toBe("price");
  });

  it("drops only the reserved compiled condition and keeps every returned value", () => {
    expect(
      unitInputs({
        inputs: { value_1: 0, field_condition: 0 },
        inputs_bool: { escalated: false },
        inputs_text: { locale: "" },
      }),
    ).toEqual([
      ["value_1", 0],
      ["escalated", false],
      ["locale", ""],
    ]);
    expect(unitInputs({ inputs: { value_1: null } })).toEqual([
      ["value_1", null],
    ]);
    expect(unitInputs(undefined)).toEqual([]);
  });

  it("reads a share recipe as one sentence, without inventing a missing value", () => {
    const recipe = {
      kind: "share",
      level: "conversation",
      inputs: [resolved],
      field_spec: {
        conditions: [{ alias: "value_1", operator: "eq", value: 1 }],
      },
    };
    const label = (alias) => inputLabel(alias, { inputs: recipe.inputs });
    expect(recipeSentence(recipe, label)).toBe(
      "Share of conversations where resolved (latest) is 1",
    );
    expect(
      recipeSentence(
        {
          ...recipe,
          field_spec: {
            conditions: [{ alias: "value_1", operator: "eq", value: null }],
          },
        },
        label,
      ),
    ).toBe("Share of conversations where resolved (latest) is …");
    expect(
      recipeSentence(
        {
          ...recipe,
          inputs: [
            {
              ...resolved,
              reducer: "any",
              condition_stage: "observation",
              condition: { operator: "ne", value: "fr" },
            },
          ],
        },
        label,
      ),
    ).toBe(
      'Share of conversations where resolved (any observation) is not "fr"',
    );
  });

  it("reads averages and summaries without claiming an unknown calculation", () => {
    expect(
      recipeSentence(
        { kind: "average", level: "conversation", inputs: [duration] },
        (alias) => inputLabel(alias, { inputs: [duration] }),
      ),
    ).toBe("Average of Duration (mean) across conversations");
    expect(definitionSummary({ kind: "share", level: "span" })).toBe(
      "Share of spans",
    );
    expect(definitionSummary({ level: "conversation" })).toBeNull();
    expect(definitionSummary(null)).toBeNull();
  });

  it("names definition enums in plain words and keeps unknown values visible", () => {
    expect(enumLabel(VALUE_TYPE_LABELS, "boolean")).toBe("Yes or no");
    expect(enumLabel(VALUE_FORMAT_LABELS, "percent")).toBe("Percentage");
    expect(enumLabel(WEIGHTING_LABELS, "none")).toBe("None");
    expect(enumLabel(REDUCER_LABELS, "latest")).toBe("Latest value");
    expect(enumLabel(REDUCER_LABELS, "p99_estimate")).toBe("P99 estimate");
    expect(enumLabel(REDUCER_LABELS, undefined)).toBeNull();
    expect(enumLabel(REDUCER_LABELS, "")).toBeNull();
    expect(inputSourceKind(resolved)).toBe("Span attribute");
    expect(inputSourceKind(duration)).toBe("Span field");
    expect(inputSourceKind({ source: "eval", config_id: "eval-1" })).toBe(
      "Evaluation",
    );
  });
});
