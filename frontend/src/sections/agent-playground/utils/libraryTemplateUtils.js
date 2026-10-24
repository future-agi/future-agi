import { mapVersionToFormConfig } from "./promptVersionUtils";

// Shared prompt-library templates cross a trust boundary before becoming
// runnable Agent Builder nodes. Keep import narrowing in this adapter so
// UI components consume already-detached prompt-node config.
const SUPPORTED_PROMPT_ROLES = new Set(["system", "user", "assistant"]);
const SUPPORTED_CONTENT_TYPES = new Set(["text"]);
const SUPPORTED_LIBRARY_OUTPUT_FORMATS = new Set(["string", "json"]);
const SUPPORTED_LIBRARY_TEMPLATE_FORMATS = new Set(["mustache", "jinja"]);
const SUPPORTED_LIBRARY_RESPONSE_FORMATS = new Set([
  "text",
  "json",
  "json_object",
  "json_schema",
  "string",
]);
const CONFIGURATION_KEYS_TO_LIFT = [
  "model",
  "model_detail",
  "response_format",
  "responseFormat",
  "response_schema",
  "responseSchema",
  "output_format",
  "outputFormat",
  "temperature",
  "max_tokens",
  "top_p",
  "frequency_penalty",
  "presence_penalty",
  "template_format",
  "templateFormat",
  "reasoning",
  "tools",
  "tool_choice",
  "toolChoice",
];
const STRIPPED_LIBRARY_CONFIGURATION_KEYS = [
  "tools",
  "tool_choice",
  "toolChoice",
];
const DISALLOWED_RESPONSE_SCHEMA_KEYS = new Set([
  "$ref",
  "$recursiveRef",
  "$dynamicRef",
]);
const MAX_LIBRARY_RESPONSE_SCHEMA_BYTES = 50_000;
const MAX_LIBRARY_RESPONSE_SCHEMA_DEPTH = 12;
const MAX_LIBRARY_RESPONSE_SCHEMA_NODES = 1_000;
const MAX_LIBRARY_RESPONSE_SCHEMA_ARRAY_LENGTH = 200;

export const INCOMPATIBLE_LIBRARY_TEMPLATE_MESSAGE =
  "This library template can't be added because Agent Builder currently supports text-only library prompt templates with string or JSON outputs.";

function isPlainObject(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function hasSafeResponseSchemaShape(schema) {
  if (schema == null) return true;
  if (!isPlainObject(schema)) return false;

  let serialized;
  try {
    serialized = JSON.stringify(schema);
  } catch {
    return false;
  }

  if (
    new TextEncoder().encode(serialized).length >
    MAX_LIBRARY_RESPONSE_SCHEMA_BYTES
  ) {
    return false;
  }

  let visitedNodes = 0;
  const seen = new WeakSet();

  function visit(value, depth) {
    if (value == null || typeof value !== "object") return true;
    if (depth > MAX_LIBRARY_RESPONSE_SCHEMA_DEPTH) return false;
    if (seen.has(value)) return false;
    seen.add(value);
    visitedNodes += 1;
    if (visitedNodes > MAX_LIBRARY_RESPONSE_SCHEMA_NODES) return false;

    if (Array.isArray(value)) {
      if (value.length > MAX_LIBRARY_RESPONSE_SCHEMA_ARRAY_LENGTH) {
        return false;
      }
      return value.every((item) => visit(item, depth + 1));
    }

    return Object.entries(value).every(([key, child]) => {
      if (DISALLOWED_RESPONSE_SCHEMA_KEYS.has(key)) return false;
      return visit(child, depth + 1);
    });
  }

  return visit(schema, 0);
}

function normalizeTemplateContentBlock(block) {
  if (typeof block === "string") {
    return { type: "text", text: block };
  }

  if (
    !block ||
    typeof block !== "object" ||
    !SUPPORTED_CONTENT_TYPES.has(block.type)
  ) {
    return null;
  }

  if (block.type === "text") {
    return typeof block.text === "string"
      ? { type: "text", text: block.text }
      : null;
  }

  return null;
}

function normalizeTemplateContent(content) {
  if (typeof content === "string") {
    return [{ type: "text", text: content }];
  }

  if (!Array.isArray(content) || content.length === 0) {
    return null;
  }

  const normalizedContent = content.map(normalizeTemplateContentBlock);

  if (normalizedContent.some((block) => block === null)) {
    return null;
  }

  return normalizedContent;
}

function normalizeTemplateConfiguration(snapshot) {
  if (
    snapshot.configuration !== undefined &&
    snapshot.configuration !== null &&
    (typeof snapshot.configuration !== "object" ||
      Array.isArray(snapshot.configuration))
  ) {
    return null;
  }

  const liftedConfiguration = CONFIGURATION_KEYS_TO_LIFT.reduce(
    (config, key) => {
      if (snapshot[key] !== undefined) {
        return { ...config, [key]: snapshot[key] };
      }
      return config;
    },
    {},
  );

  const configuration = {
    ...liftedConfiguration,
    ...(snapshot.configuration || {}),
  };

  STRIPPED_LIBRARY_CONFIGURATION_KEYS.forEach((key) => {
    delete configuration[key];
  });

  const outputFormat =
    configuration.output_format || configuration.outputFormat || "string";
  if (!SUPPORTED_LIBRARY_OUTPUT_FORMATS.has(outputFormat)) {
    return null;
  }
  configuration.output_format = outputFormat;
  delete configuration.outputFormat;

  const templateFormat =
    configuration.template_format || configuration.templateFormat || "mustache";
  if (!SUPPORTED_LIBRARY_TEMPLATE_FORMATS.has(templateFormat)) {
    return null;
  }
  configuration.template_format = templateFormat;
  delete configuration.templateFormat;

  const responseFormat =
    configuration.response_format || configuration.responseFormat || "text";
  if (
    typeof responseFormat !== "string" ||
    !SUPPORTED_LIBRARY_RESPONSE_FORMATS.has(responseFormat)
  ) {
    return null;
  }
  configuration.response_format = responseFormat;
  delete configuration.responseFormat;

  const responseSchema =
    configuration.response_schema ?? configuration.responseSchema ?? null;
  if (responseFormat === "json_schema") {
    if (!hasSafeResponseSchemaShape(responseSchema)) {
      return null;
    }
    if (responseSchema !== null) {
      configuration.response_schema = responseSchema;
    }
  } else {
    delete configuration.response_schema;
  }
  delete configuration.responseSchema;

  return configuration;
}

export function normalizeLibraryTemplateSnapshot(template) {
  const rawSnapshot = template?.prompt_config_snapshot;
  const snapshot = Array.isArray(rawSnapshot) ? rawSnapshot[0] : rawSnapshot;

  if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) {
    return null;
  }

  const configuration = normalizeTemplateConfiguration(snapshot);
  if (!configuration) {
    return null;
  }

  if (!Array.isArray(snapshot.messages) || snapshot.messages.length === 0) {
    return null;
  }

  const normalizedMessages = snapshot.messages.map((message) => {
    if (
      !message ||
      typeof message !== "object" ||
      !SUPPORTED_PROMPT_ROLES.has(message.role)
    ) {
      return null;
    }

    const normalizedContent = normalizeTemplateContent(message.content);
    if (!normalizedContent) return null;

    return {
      ...message,
      role: message.role,
      content: normalizedContent,
    };
  });

  if (normalizedMessages.some((message) => message === null)) {
    return null;
  }

  return {
    ...snapshot,
    configuration,
    messages: normalizedMessages,
  };
}

export function toDetachedLibraryTemplateConfig(template) {
  const promptConfigSnapshot = normalizeLibraryTemplateSnapshot(template);
  if (!promptConfigSnapshot) return null;

  return {
    prompt_template_id: null,
    prompt_version_id: null,
    ...mapVersionToFormConfig({
      prompt_config_snapshot: promptConfigSnapshot,
    }),
  };
}
