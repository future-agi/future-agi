// An object read as chat because it holds
// a `turns` / `messages` list is stored as an object, so the Schema drawer
// infers `object` for it (not `array`), its Example is the stored object (not
// the normalised messages), Declare (From inferred) writes `type: "object"`
// with the nested keys, and the rows the schema was inferred from pass it.
import { describe, expect, it } from "vitest";
import { WRAPPED_CONVERSATIONS as sessions } from "src/test-fixtures/wrapped-conversations";
import { inferColumns, inferPaths } from "../inferSchema";
import { schemaFromInferred } from "../presets";
import { validateRow, validateValue } from "../schemaValidate";

const payloads = [
  {
    model: "gpt-4o-mini",
    temperature: 0.2,
    messages: [
      { role: "system", content: "You are a support agent." },
      { role: "user", content: "Where is my refund?" },
    ],
  },
  {
    model: "gpt-4o-mini",
    temperature: 0,
    messages: [{ role: "user", content: "Cancel order A-5102." }],
  },
];

const declareFrom = (name, values, { asText = false } = {}) => {
  const col = { id: "c1", name, data_type: "json" };
  const rows = values.map((v) => ({ c1: asText ? JSON.stringify(v) : v }));
  const entries = inferColumns([col], rows);
  const nested = {
    [name]: inferPaths(
      rows.map((r) => r.c1),
      { column: name, edgeIndexes: false, maxDepth: 4, maxPaths: 60 },
    ),
  };
  return { entry: entries[0], schema: schemaFromInferred(entries, nested) };
};

describe("Schema drawer · a wrapped conversation is an object (RV-02 review 2)", () => {
  it.each([
    [
      "the ES sessions",
      "conversation",
      sessions,
      ["turns", "resolution", "failing_turn", "channel"],
    ],
    [
      "request payloads",
      "request",
      payloads,
      ["model", "temperature", "messages"],
    ],
  ])(
    "%s: inferred object, Example is the stored value, schema object, rows pass",
    (_, name, values, keys) => {
      [false, true].forEach((asText) => {
        const { entry, schema } = declareFrom(name, values, { asText });
        expect(entry.kinds).toEqual({ object: 1 });
        expect(entry.kinds.array).toBeUndefined();
        // The Example keeps the stored keys and roles.
        keys.forEach((k) => expect(entry.example).toHaveProperty(k));
        expect(entry.example).toEqual(values[0]);
        const prop = schema.properties[name];
        expect(prop.type).toBe("object");
        // Nested keys are inferred again (they were gated on kinds.object).
        keys.forEach((k) => expect(prop.properties).toHaveProperty(k));
        values.forEach((v) => {
          expect(validateValue(v, prop)).toEqual([]);
          expect(validateValue({ [name]: v }, schema)).toEqual([]);
          expect(
            validateRow({ [name]: asText ? JSON.stringify(v) : v }, schema, {
              columns: [{ name, json: true }],
            }).issues,
          ).toEqual([]);
        });
      });
    },
  );

  it("the ES Example keeps the stored roles (customer / agent), not user / assistant", () => {
    const { entry } = declareFrom("conversation", sessions);
    const roles = entry.example.turns.map((t) => t.role);
    expect(roles).toContain("customer");
    expect(roles).toContain("agent");
    expect(roles).not.toContain("user");
    expect(roles).not.toContain("assistant");
  });

  it("a bare message list is still an array", () => {
    const { entry, schema } = declareFrom(
      "messages",
      payloads.map((p) => p.messages),
    );
    expect(entry.kinds).toEqual({ array: 1 });
    expect(schema.properties.messages.type).toBe("array");
    payloads.forEach((p) =>
      expect(validateValue(p.messages, schema.properties.messages)).toEqual([]),
    );
  });
});
