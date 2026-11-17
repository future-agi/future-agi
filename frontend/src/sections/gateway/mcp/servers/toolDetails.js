// What the gateway's tool inventory says about one tool, read in one place
// for the Tools list: its description, version, deprecation, annotations and
// input schema. A server that sends none of them leaves each empty.

const text = (value) => (typeof value === "string" ? value.trim() : "");

// The tool's input schema as an object, or null when it has none or it is not
// a JSON object. Older data may carry it as JSON text or under MCP's own name.
export function toolInputSchema(tool) {
  let schema = tool?.input_schema ?? tool?.inputSchema;
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

// The tool's description, version label and deprecation, each empty when
// absent.
export function toolFacts(tool) {
  const version = text(tool?.version);
  return {
    description: text(tool?.description),
    version: version && !/^v/i.test(version) ? `v${version}` : version,
    deprecated: tool?.deprecated === true,
    deprecationMessage: text(tool?.deprecation_message),
    replacedBy: text(tool?.replaced_by),
  };
}

// The line a Deprecated mark shows on hover.
export function deprecationHint(facts) {
  if (facts.deprecationMessage) return facts.deprecationMessage;
  if (facts.replacedBy) return `Use ${facts.replacedBy} instead`;
  return "This tool is deprecated";
}

const HINTS = [
  ["readOnlyHint", "Read-only", "neutral"],
  ["destructiveHint", "Destructive", "error"],
  ["idempotentHint", "Idempotent", "neutral"],
  ["openWorldHint", "Open world", "neutral"],
];

// The MCP annotations the tool's server set to true, as labelled marks.
export function annotationMarks(annotations) {
  if (!annotations || typeof annotations !== "object") return [];
  const marks = [];
  HINTS.forEach(([key, label, tone]) => {
    if (annotations[key] === true) marks.push({ key, label, tone });
  });
  return marks;
}
