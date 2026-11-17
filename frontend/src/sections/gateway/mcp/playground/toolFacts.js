// What the gateway's per-organization tool inventory says about one tool,
// read in one place: its description, version, deprecation and input schema.
// A server that sends none of them leaves each empty.

// The tool's input schema as an object, or null when it has none or it is not
// a JSON object (a server may send it as JSON text).
export function toolSchema(tool) {
  let schema = tool?.input_schema;
  if (typeof schema === "string") {
    try {
      schema = JSON.parse(schema);
    } catch {
      return null;
    }
  }
  return schema && typeof schema === "object" && !Array.isArray(schema)
    ? schema
    : null;
}

const STARTING_VALUES = {
  string: "",
  number: 0,
  integer: 0,
  boolean: false,
  array: [],
  object: {},
};

// The arguments a run starts from: one entry per schema property, with an
// empty value of its type (null for an untyped property), as JSON text. Null
// when the schema names no properties.
export function startingArguments(schema) {
  const properties = schema?.properties;
  if (!properties || typeof properties !== "object") return null;
  const names = Object.keys(properties);
  if (names.length === 0) return null;
  const values = {};
  names.forEach((name) => {
    const type = properties[name]?.type;
    values[name] = Object.prototype.hasOwnProperty.call(STARTING_VALUES, type)
      ? STARTING_VALUES[type]
      : null;
  });
  return JSON.stringify(values, null, 2);
}

// The tool's description, version and deprecation, each empty when absent.
export function toolFacts(tool) {
  const text = (value) => (typeof value === "string" ? value.trim() : "");
  const version = text(tool?.version);
  return {
    description: text(tool?.description),
    version: version && !/^v/i.test(version) ? `v${version}` : version,
    deprecated: tool?.deprecated === true,
    deprecationNote: [
      text(tool?.deprecation_message),
      text(tool?.replaced_by) && `Replaced by ${text(tool.replaced_by)}.`,
    ]
      .filter(Boolean)
      .join(" "),
  };
}
