// Schema presets (R2-SPEC P6 §3.4) for the Schema drawer: row schemas keyed
// by column name. Only columns the dataset has are checked
// (schemaValidate.validateRow).

const MESSAGES = {
  type: "array",
  items: {
    type: "object",
    required: ["role"],
    properties: {
      role: { enum: ["system", "user", "assistant", "tool"] },
      content: { type: ["string", "array", "null"] },
      tool_calls: {
        type: "array",
        items: {
          type: "object",
          required: ["function"],
          properties: {
            function: {
              type: "object",
              required: ["name"],
              properties: { name: { type: "string" } },
            },
          },
        },
      },
    },
  },
};

export const SCHEMA_PRESETS = [
  {
    id: "chat",
    name: "Chat dataset",
    hint: "OpenAI messages + expected",
    schema: {
      type: "object",
      required: ["conversation"],
      properties: { conversation: MESSAGES, expected: { type: "object" } },
    },
  },
  {
    id: "rag",
    name: "RAG",
    hint: "query, contexts[], answer",
    schema: {
      type: "object",
      required: ["query", "contexts", "answer"],
      properties: {
        query: { type: "string" },
        contexts: { type: "array", items: { type: "string" } },
        answer: { type: "string" },
      },
    },
  },
  {
    id: "tool_use",
    name: "Tool use",
    hint: "messages + tools[] + expected tool calls",
    schema: {
      type: "object",
      required: ["conversation"],
      properties: {
        conversation: MESSAGES,
        tools: { type: "array", items: { type: "object", required: ["name"] } },
        expected: {
          type: "object",
          properties: {
            tool_calls: {
              type: "array",
              items: { type: "object", required: ["name"] },
            },
          },
        },
      },
    },
  },
  {
    id: "multimodal",
    name: "Multimodal",
    hint: "images[], audio, documents[]",
    schema: {
      type: "object",
      properties: {
        screenshot: { type: "string" },
        photos: { type: "array", items: { type: "string" } },
        call_audio: { type: "string" },
        contract: { type: "string" },
      },
    },
  },
];

export const presetById = (id) =>
  SCHEMA_PRESETS.find((p) => p.id === id) ?? null;

/** Row schema from inferred columns (the `Declare` of the Inferred tab). */
export function schemaFromInferred(columnEntries, nestedByColumn = {}) {
  const properties = {};
  const required = [];
  columnEntries.forEach((c) => {
    const kinds = Object.keys(c.kinds ?? {}).filter((k) => k !== "null");
    if (!kinds.length) return;
    const s = {
      type: kinds.length === 1 ? kinds[0] : kinds,
    };
    if (c.enum_values?.length && kinds.length === 1 && kinds[0] === "string")
      s.enum = c.enum_values;
    const nested = nestedByColumn[c.path];
    if (nested && kinds.includes("object")) {
      const props = {};
      nested
        .filter((p) => /^[A-Za-z_][\w-]*$/.test(p.rel))
        .forEach((p) => {
          const k = Object.keys(p.kinds).filter((x) => x !== "null");
          if (k.length) props[p.rel] = { type: k.length === 1 ? k[0] : k };
        });
      if (Object.keys(props).length) s.properties = props;
    }
    properties[c.path] = s;
    if (c.coverage?.present === c.coverage?.total) required.push(c.path);
  });
  return { type: "object", required, properties };
}
