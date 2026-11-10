import { describe, expect, it } from "vitest";
import {
  conditionClause,
  conditionParts,
  definitionSummary,
  enumLabel,
  inputLabel,
  inputSourceKind,
  previewNeeds,
  recipeSentence,
  recipeSentenceParts,
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

  it("names one condition in the preview sentence's words, keeping a missing value visible", () => {
    const recipe = {
      kind: "share",
      level: "conversation",
      inputs: [resolved],
      field_spec: {
        conditions: [{ alias: "value_1", operator: "eq", value: 1 }],
      },
    };
    const label = (alias) => inputLabel(alias, { inputs: recipe.inputs });
    expect(conditionClause(recipe, resolved, label)).toBe(
      "resolved (latest) is 1",
    );
    expect(recipeSentence(recipe, label)).toBe(
      `Share of conversations where ${conditionClause(recipe, resolved, label)}`,
    );
    const empty = {
      ...recipe,
      field_spec: {
        conditions: [{ alias: "value_1", operator: "gt", value: null }],
      },
    };
    expect(conditionClause(empty, resolved, label)).toBe(
      "resolved (latest) is above …",
    );
    expect(conditionClause(empty, resolved, label)).not.toMatch(/\b0\b/);
  });

  it("asks the preview only for the piece that is still missing", () => {
    const share = {
      project_id: "project-a",
      kind: "share",
      inputs: [resolved],
      field_spec: {
        conditions: [{ alias: "value_1", operator: "eq", value: null }],
      },
    };
    expect(previewNeeds({ ...share, project_id: "" })).toBe(
      "Choose a project to see a preview.",
    );
    expect(previewNeeds({ ...share, inputs: [] })).toBe(
      "Choose a source to see a preview.",
    );
    expect(previewNeeds({ ...share, kind: "custom", formula: " " })).toBe(
      "Enter a formula to see a preview.",
    );
    const label = (alias) => inputLabel(alias, { inputs: share.inputs });
    // The field is named, with the one verb the footer uses.
    expect(previewNeeds(share, label)).toBe(
      "Choose a value for resolved to see a preview.",
    );
    expect(previewNeeds(share)).toBe(
      "Choose a value for value_1 to see a preview.",
    );
    expect(
      previewNeeds({
        ...share,
        field_spec: {
          conditions: [{ alias: "value_1", operator: "eq", value: 1 }],
        },
      }),
    ).toBe("Finish the calculation to see a preview.");
    for (const form of [share, { ...share, inputs: [] }])
      expect(previewNeeds(form)).not.toMatch(/project/i);
  });

  it("names the field the save checks flag, by the same rule", () => {
    const locale = {
      alias: "value_2",
      source: "span",
      field: "attribute_text",
      attribute_key: "locale",
      value_type: "text",
      reducer: "latest",
    };
    const form = (resolvedValue, localeValue) => ({
      project_id: "project-a",
      kind: "share",
      inputs: [resolved, locale],
      field_spec: {
        conditions: [
          { alias: "value_1", operator: "eq", value: resolvedValue },
          { alias: "value_2", operator: "eq", value: localeValue },
        ],
      },
    });
    const label = (alias) => inputLabel(alias, { inputs: [resolved, locale] });
    expect(previewNeeds(form(1, null), label)).toBe(
      "Choose a value for locale to see a preview.",
    );
    // Not a number yet: the same field the footer names.
    expect(previewNeeds(form(Number.NaN, "fr"), label)).toBe(
      "Choose a value for resolved to see a preview.",
    );
    // An empty string is a real text value the server compares with, so it is
    // never reported as missing; the earlier missing number is named instead.
    expect(previewNeeds(form(null, ""), label)).toBe(
      "Choose a value for resolved to see a preview.",
    );
    expect(previewNeeds(form(1, ""), label)).toBe(
      "Finish the calculation to see a preview.",
    );
  });

  it("marks only a value not chosen yet as missing, and says empty text plainly", () => {
    const recipe = (value) => ({
      kind: "share",
      level: "conversation",
      inputs: [resolved],
      field_spec: { conditions: [{ alias: "value_1", operator: "eq", value }] },
    });
    expect(conditionParts(recipe(null), resolved)).toEqual({
      subject: "value_1 (latest)",
      operator: "is",
      value: "…",
      missing: true,
    });
    for (const value of [0, false])
      expect(conditionParts(recipe(value), resolved)).toMatchObject({
        value: String(value),
        missing: false,
      });
    expect(conditionParts(recipe(""), resolved)).toMatchObject({
      value: "empty text",
      missing: false,
    });
    expect(conditionClause(recipe(""), resolved)).toBe(
      "value_1 (latest) is empty text",
    );
    expect(conditionClause(recipe("fr"), resolved)).toBe(
      'value_1 (latest) is "fr"',
    );
    expect(recipeSentenceParts(recipe(null), (alias) => alias)).toEqual({
      lead: "Share of conversations",
      clauses: [conditionParts(recipe(null), resolved)],
    });
  });
});
