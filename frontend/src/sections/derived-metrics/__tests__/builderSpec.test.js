import { writeFileSync } from "node:fs";
import { env } from "node:process";
import { describe, expect, it } from "vitest";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";
import {
  builderFromMetric,
  builderProblems,
  builderWrite,
  changeKind,
  newBuilder,
  previewDefinition,
  rebuildAsUser,
  selectSource,
  setRecipeInputs,
  supportedSourceOptions,
} from "../builderSpec";
import { capabilities, catalog, evaluators, id } from "./fixtures/builder";
import wireFixture from "./fixtures/builder-wire.json";

const options = supportedSourceOptions(catalog, evaluators, capabilities);
const recipe = (kind = "share", source = 0) =>
  selectSource(
    {
      ...newBuilder(id),
      name: "Typed metric",
      kind,
      value_format: kind === "share" ? "percent" : "number",
    },
    0,
    options[source],
    capabilities,
  );
const condition = (form, value, operator = "eq") =>
  setRecipeInputs(form, form.inputs, [
    { alias: form.inputs[0].alias, operator, value },
  ]);
const saved = (definition) => ({
  id,
  project_id: id,
  name: "Metric",
  description: "Preserve",
  current_version: 4,
  definition: null,
  draft_definition: {
    ...definition,
    id,
    version: 4,
    definition_hash: "server-owned",
    needs_rebuild: false,
  },
});

describe("builder typed wire", () => {
  it.each(capabilities.kinds)(
    "round-trips %s without provenance or semantic loss",
    (kind) => {
      const form =
        kind === "share" || kind === "count"
          ? condition(recipe(kind), "fr")
          : {
              ...recipe(kind, 2),
              formula: "sum(value_1)",
              field_spec: {
                inputs: recipe(kind, 2).inputs,
                value_alias: "value_1",
                ...(kind === "percentile" ? { percentile: 0.9 } : {}),
              },
            };
      const metric = saved(form);
      expect(previewDefinition(builderFromMetric(metric))).toEqual(
        previewDefinition(form),
      );
      expect(builderWrite(builderFromMetric(metric), metric)).toEqual({
        ...previewDefinition(form),
        project_id: id,
        name: "Metric",
        description: "Preserve",
        expected_version: 4,
      });
    },
  );
  it.each(
    capabilities.sources.flatMap((source) =>
      source.operators.map((operator) => [source.value_type, operator]),
    ),
  )("validates %s %s comparisons", (type, operator) => {
    const index = { text: 0, boolean: 1, number: 2 }[type];
    const form = condition(
      recipe("share", index),
      { text: "fr", boolean: false, number: 0 }[type],
      operator,
    );
    expect(builderProblems(form, capabilities)).toEqual([]);
    const result = validateContractedRequestConfig({
      method: "post",
      url: "/tracer/derived-metrics/",
      data: builderWrite(form),
    });
    expect(result.ok, result.error?.message).toBe(true);
    expect(result.skipped).not.toBe(true);
  });
  it.each([null, undefined, NaN, "0", false])(
    "rejects a nonnumeric comparison %s without coercing zero",
    (value) => {
      expect(
        builderProblems(
          condition(recipe("share", 2), value),
          capabilities,
        ).join(" "),
      ).toContain("number comparison");
    },
  );
  it.each(["first", "latest", "sum", "count"])(
    "retains reducer %s",
    (reducer) => {
      const form = recipe("total", 2);
      form.inputs[0].reducer = reducer;
      expect(builderProblems(form, capabilities)).toEqual([]);
      expect(builderFromMetric(saved(form)).inputs[0].reducer).toBe(reducer);
    },
  );
  it.each(["any", "all"])(
    "preserves explicit %s observation comparison and locale text",
    (reducer) => {
      const form = condition(recipe(), true);
      Object.assign(form.inputs[0], {
        reducer,
        condition_stage: "observation",
        condition: { operator: "eq", value: "fr" },
      });
      expect(builderProblems(form, capabilities)).toEqual([]);
      expect(previewDefinition(builderFromMetric(saved(form)))).toEqual(
        previewDefinition(form),
      );
    },
  );
  it("names an input by the label it is given in messages, defaulting to its alias", () => {
    const form = condition(recipe("share", 2), null);
    const alias = form.inputs[0].alias;
    expect(builderProblems(form, capabilities)).toContain(
      `Choose a number comparison value for ${alias}.`,
    );
    const named = builderProblems(form, capabilities, {
      labelFor: (name) => (name === alias ? "Price" : name),
    });
    expect(named).toContain("Choose a number comparison value for Price.");
    expect(named.join(" ")).not.toContain(alias);
  });
  it("refuses unsupported type/reducer/operator pairs and same-span predicates", () => {
    const form = condition(recipe(), "fr", "gte");
    form.inputs[0].reducer = "sum";
    expect(builderProblems(form, capabilities).join(" ")).toMatch(
      /Unsupported reducer.*Unsupported comparison/,
    );
    Object.assign(form.inputs[0], {
      reducer: "any",
      condition_stage: "observation",
      condition: { operator: "eq", right_alias: "second" },
    });
    expect(builderProblems(form, capabilities)).toContain(
      "Observation conditions must use a single source.",
    );
  });
  it("never includes derived metric fields or ambiguous/raw unsupported types", () => {
    const result = supportedSourceOptions(
      {
        result: {
          metrics: [
            { ...catalog.result.metrics[0], property_kind: "metric_field" },
            {
              ...catalog.result.metrics[0],
              attribute_types: ["number", "string"],
            },
          ],
        },
      },
      null,
      capabilities,
    );
    expect(result).toEqual([]);
  });
  it("preserves legacy custom formula inputs, filters, weighting and absence of typed ordering", () => {
    const form = {
      formula: "ratio(sum(score), count(score))",
      inputs: [
        {
          alias: "score",
          source: "eval",
          config_id: id,
          config_hash: "a".repeat(64),
          reducer: "latest",
        },
      ],
      selection_unit: "session",
      weighting: "inverse_probability",
      filters: [{ field: "model", value: "model-a" }],
      cohort_key: "locale",
    };
    expect(previewDefinition(builderFromMetric(saved(form)))).toEqual(form);
  });
  it("rebuild is explicit, user-only and never copies legacy raw-span semantics", () => {
    const old = {
      ...newBuilder(id),
      name: "Legacy",
      kind: "custom",
      formula: "sum(value)",
      selection_unit: "user",
    };
    const next = rebuildAsUser(old);
    expect(next).toMatchObject({
      name: "Legacy",
      kind: "share",
      level: "user",
      selection_unit: "user",
      user_rule: "any",
      inputs: [],
    });
    expect(old.kind).toBe("custom");
  });
  it("removes boolean labels when explicitly changing to a numeric kind", () => {
    expect(
      changeKind(
        { ...recipe(), value_labels: { match: "Resolved", other: "Open" } },
        "average",
      ).value_labels,
    ).toEqual({});
  });
  it("uses unique aliases after deleting a middle input", () => {
    const form = recipe();
    form.inputs[0].alias = "value_2";
    expect(
      selectSource(form, 1, options[1], capabilities).inputs.map(
        (input) => input.alias,
      ),
    ).toEqual(["value_2", "value_3"]);
  });
  it("exports actual builder payloads for the backend serializer/compiler round trip", () => {
    const wires = [];
    for (const operator of capabilities.sources[0].operators)
      wires.push(builderWrite(condition(recipe("share", 2), 0, operator)));
    for (const operator of ["eq", "ne"])
      wires.push(builderWrite(condition(recipe(), "fr", operator)));
    for (const reducer of ["any", "all"]) {
      const form = condition(recipe(), true);
      Object.assign(form.inputs[0], {
        reducer,
        condition_stage: "observation",
        condition: { operator: "eq", value: "fr" },
      });
      wires.push(builderWrite(form));
    }
    for (const reducer of ["first", "latest", "sum", "count"]) {
      const form = recipe("total", 2);
      form.inputs[0].reducer = reducer;
      wires.push(builderWrite(form));
    }
    let mixed = condition(recipe(), "fr");
    mixed = selectSource(mixed, 1, options[3], capabilities);
    mixed.field_spec.conditions[1].value = 0;
    wires.push(builderWrite(mixed));
    wires.push(builderWrite(condition(recipe("count", 1), false)));
    expect(wires).toHaveLength(16);
    expect(wires).toEqual(wireFixture);
    // Only the cloud script opts in. This is evidence consumed by real backend
    // serializers/compiler tests, not production data or an endpoint stub.
    if (env.F05_WIRE_OUT)
      writeFileSync(env.F05_WIRE_OUT, JSON.stringify(wires));
  });
});
