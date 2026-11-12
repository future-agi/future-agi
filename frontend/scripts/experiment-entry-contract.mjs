// Swagger 2 exposes flat properties for the runtime admission reader and an
// x-oneOf for generators. Upgrade that union only AFTER Swagger validation.
// In particular an HTTP entry must never inherit prompt/model defaults.
function upgrade(schema) {
  if (!schema || typeof schema !== "object") return schema;
  if (Array.isArray(schema)) return schema.map(upgrade);
  if (schema["x-json-value"]) return {};
  let result = Object.fromEntries(Object.entries(schema).map(([key, value]) => [
    key, upgrade(value),
  ]));
  // Swagger converters need not rewrite references inside vendor extensions.
  if (typeof result.$ref === "string")
    result.$ref = result.$ref.replace(/^#\/definitions\//, "#/components/schemas/");
  const nullable = result["x-nullable"] === true;
  delete result["x-nullable"];
  if (result["x-oneOf"])
    result = { description: result.description, oneOf: result["x-oneOf"] };
  return nullable ? { anyOf: [result, { type: "null" }] } : result;
}

export function normalizeExperimentEntrySchema(schema) {
  const properties = schema?.properties;
  // DRF also declares the HTTP fields on its legacy serializer for discovery.
  // Reconstruct the union if a fresh Django export emits only that flat shape.
  if (!schema?.["x-oneOf"] && properties?.target_type?.enum?.includes("http")) {
    const fields = ["id", "name", "target_type", "http", "variable_mapping"];
    if (fields.some((field) => !properties[field]))
      throw new Error("PromptConfigEntry is missing a declared HTTP field.");
    const http = Object.fromEntries(fields.map((field) => [field, properties[field]]));
    http.name = { ...http.name, maxLength: 2000 };
    http.target_type = { ...http.target_type, enum: ["http"] };
    const variants = [
      { type: "object", required: ["target_type", "http"], additionalProperties: false, properties: http },
    ];
    // An A2A agent entry (target_type "a2a" with its `a2a` settings) has its
    // own serializer, which the server turns into an HTTP source.
    if (properties.target_type.enum.includes("a2a")) {
      const a2aFields = ["id", "name", "target_type", "a2a"];
      if (a2aFields.some((field) => !properties[field]))
        throw new Error("PromptConfigEntry is missing a declared A2A field.");
      const a2a = Object.fromEntries(a2aFields.map((field) => [field, properties[field]]));
      a2a.name = { ...a2a.name, maxLength: 2000 };
      a2a.target_type = { ...a2a.target_type, enum: ["a2a"] };
      variants.push({ type: "object", required: ["target_type", "a2a"], additionalProperties: false, properties: a2a });
    }
    const legacy = Object.fromEntries(Object.entries(properties).filter(([field]) =>
      !["http", "variable_mapping", "a2a"].includes(field),
    ));
    legacy.target_type = { ...legacy.target_type, enum: ["prompt", "agent"] };
    schema = { ...schema, "x-oneOf": [...variants, { type: "object", properties: legacy }] };
  }
  return upgrade(schema);
}
