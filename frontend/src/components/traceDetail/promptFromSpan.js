import { groupFlattenedMessageAttrs } from "src/components/traceDetailDrawer/DrawerRightRenderer/flattenedMessageAttrs";

const INPUT_MESSAGE_PREFIXES = [
  "llm.input_messages",
  "llm.inputMessages",
  "gen_ai.input.messages",
];

const MODEL_KEYS = [
  "gen_ai.request.model",
  "gen_ai.response.model",
  "llm.model_name",
  "ls_model_name",
];

const PROVIDER_KEYS = [
  "gen_ai.provider.name",
  "gen_ai.system",
  "llm.provider",
  "ls_provider",
];

const REQUEST_PARAM_KEYS = {
  temperature: "temperature",
  top_p: "top_p",
  max_tokens: "max_tokens",
  max_output_tokens: "max_tokens",
  frequency_penalty: "frequency_penalty",
  presence_penalty: "presence_penalty",
  seed: "seed",
};

function parseTemplateVariables(attrs) {
  const raw =
    attrs?.["gen_ai.prompt.template.variables"] ||
    attrs?.["llm.prompt_template.variables"];
  if (!raw) return null;
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      return parsed;
  } catch {
    /* ignore */
  }
  return null;
}

const MIN_TEMPLATIZE_LEN = 2;

function escapeRegExp(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Replace resolved variable values in text with {{varName}} placeholders.
// Longer values first so a value that is a substring of another isn't clobbered.
function templatizeText(text, variables) {
  if (!text || !variables) return text;
  let result = text;
  const entries = Object.entries(variables)
    .filter(([, val]) => val != null && String(val).trim().length >= MIN_TEMPLATIZE_LEN)
    .sort((a, b) => String(b[1]).length - String(a[1]).length);
  for (const [name, value] of entries) {
    const strVal = String(value);
    const left = /\w/.test(strVal[0]) ? "\\b" : "";
    const right = /\w/.test(strVal[strVal.length - 1]) ? "\\b" : "";
    const re = new RegExp(`${left}${escapeRegExp(strVal)}${right}`, "g");
    result = result.replace(re, () => `{{${name}}}`);
  }
  return result;
}

function stringifyContent(content) {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        if (part?.text != null) return part.text;
        if (part?.content != null)
          return typeof part.content === "string"
            ? part.content
            : JSON.stringify(part.content);
        return JSON.stringify(part);
      })
      .filter((s) => s !== "" && s != null)
      .join("\n");
  }
  if (typeof content === "object") return JSON.stringify(content);
  return String(content);
}

// Map provider role vocab onto the workbench's user/assistant/system.
function normalizeRole(role) {
  if (typeof role !== "string") return role;
  const r = role.toLowerCase();
  if (r === "model" || r === "chatbot" || r === "ai") return "assistant";
  if (r === "human") return "user";
  return r;
}

function normalizeMessageObject(msg) {
  if (!msg || typeof msg !== "object") return null;
  const role = msg.role || msg.message?.role;
  const rawContent =
    msg.content ??
    (typeof msg.message === "string" ? msg.message : msg.message?.content);
  const rawParts = Array.isArray(msg.parts)
    ? msg.parts
    : Array.isArray(msg.message?.parts)
      ? msg.message.parts
      : null;
  let content;
  if (rawContent != null) content = stringifyContent(rawContent);
  else if (rawParts) content = stringifyContent(rawParts);
  else content = "";
  return role ? { role: normalizeRole(role), content } : null;
}

function fromFlattenedAttrs(attrs) {
  return groupFlattenedMessageAttrs(attrs, "input")
    .map(({ entries }) => {
      const m = { role: undefined, content: null, parts: {} };
      for (const { property: prop, value } of entries) {
        if (prop === "message.role" || prop === "role") {
          m.role = value;
          continue;
        }
        // nested structured content: (message.)contents.N.message_content.<field>
        const nested = prop.match(
          /^(?:message\.)?contents?\.(\d+)\.message_content\.(\w+)$/,
        );
        if (nested) {
          if (nested[2] === "text") m.parts[nested[1]] = value;
          continue;
        }
        if (prop === "message.content" || prop === "content") {
          m.content = value;
        }
      }

      const partKeys = Object.keys(m.parts);
      const content = partKeys.length
        ? partKeys
            .sort((a, b) => Number(a) - Number(b))
            .map((k) => m.parts[k])
            .join("\n")
        : stringifyContent(m.content);
      return m.role ? { role: normalizeRole(m.role), content } : null;
    })
    .filter(Boolean);
}

function fromArrayAttr(attrs) {
  for (const prefix of INPUT_MESSAGE_PREFIXES) {
    const value = parseMaybe(attrs[prefix]) ?? attrs[prefix];
    if (Array.isArray(value))
      return value.map(normalizeMessageObject).filter(Boolean);
  }
  return [];
}

// Many shapes keep the system prompt in a sibling field (Gemini config.system_instruction,
// OpenAI Responses instructions, Cohere preamble) rather than in the turn list.
function prependSystem(messages, sys) {
  const sysText = sys ? stringifyContent(sys.parts ?? sys) : "";
  if (sysText && !messages.some((m) => m.role === "system"))
    messages.unshift({ role: "system", content: sysText });
  return messages;
}

function fromObjectInput(input) {
  let data = input;
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch {
      return [];
    }
  }
  if (!data || typeof data !== "object") return [];
  if (Array.isArray(data))
    return data.map(normalizeMessageObject).filter(Boolean);
  if (Array.isArray(data.messages))
    return data.messages.map(normalizeMessageObject).filter(Boolean);
  if (Array.isArray(data.contents))
    return prependSystem(
      data.contents.map(normalizeMessageObject).filter(Boolean),
      data.config?.system_instruction ?? data.system_instruction,
    );
  if (Array.isArray(data.input))
    return prependSystem(
      data.input.map(normalizeMessageObject).filter(Boolean),
      data.instructions,
    );
  if (typeof data.input === "string" && data.input.trim())
    return prependSystem(
      [{ role: "user", content: data.input }],
      data.instructions,
    );
  if (Array.isArray(data.chat_history)) {
    const messages = data.chat_history
      .map(normalizeMessageObject)
      .filter(Boolean);
    if (typeof data.message === "string" && data.message.trim())
      messages.push({ role: "user", content: data.message });
    return prependSystem(messages, data.preamble);
  }
  if (typeof data.prompt === "string" && data.prompt.trim())
    return [{ role: "user", content: data.prompt }];
  return [];
}

// span.input is {} when there's no structured input; don't let it shadow input.value.
function isEmpty(v) {
  if (v == null) return true;
  if (typeof v === "string") return v.trim() === "";
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object") return Object.keys(v).length === 0;
  return false;
}

export function normalizeMessagesFromSpan(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  let messages = fromFlattenedAttrs(attrs);
  if (!messages.length) messages = fromArrayAttr(attrs);
  const input = !isEmpty(span?.input) ? span.input : attrs["input.value"];
  if (!messages.length) messages = fromObjectInput(input);
  // Plain text → one user turn, but not a stringified JSON blob (the original bug).
  if (
    !messages.length &&
    typeof input === "string" &&
    input.trim() &&
    parseMaybe(input) == null
  ) {
    messages = [{ role: "user", content: input }];
  }
  return messages;
}

export function extractModelFromSpan(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  if (span?.model) return span.model;
  for (const key of MODEL_KEYS) {
    const v = attrs[key];
    if (typeof v === "string" && v) return v;
  }
  const body = parsedRequestBody(span);
  if (typeof body?.model === "string" && body.model) return body.model;
  return "";
}

export function extractProviderFromSpan(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  if (span?.provider) return span.provider;
  for (const key of PROVIDER_KEYS) if (attrs[key]) return attrs[key];
  const params = parseMaybe(attrs["gen_ai.request.parameters"]);
  if (params?.model_provider) return params.model_provider;
  return "";
}

function parseMaybe(value) {
  if (value == null || value === "") return null;
  if (typeof value === "object") return value;
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      return null;
    }
  }
  return null;
}

// SDKs that log the full request as input.value keep model/params inside it.
function parsedRequestBody(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  const input = !isEmpty(span?.input) ? span.input : attrs["input.value"];
  const parsed = parseMaybe(input);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? parsed
    : null;
}

// Derive text/json — an unset format makes the workbench selector default to JSON.
export function extractResponseFormatFromSpan(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  let rf = parsedRequestBody(span)?.response_format;
  if (rf == null) rf = attrs["gen_ai.request.response_format"];
  const parsed = parseMaybe(rf) ?? rf;
  if (parsed == null) return "text";
  const type = typeof parsed === "object" ? parsed.type : parsed;
  return typeof type === "string" && type.toLowerCase().includes("json")
    ? "json"
    : "text";
}

// OTel/flattened attrs are stringly-typed ("0.7"); coerce so string-numerics
// aren't silently dropped. Non-numeric junk (a model name, "true") → null.
function toNumber(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

// Whitelisted numeric model params; transport/junk keys are dropped.
export function extractParamsFromSpan(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  const out = {};
  for (const [reqKey, name] of Object.entries(REQUEST_PARAM_KEYS)) {
    const n = toNumber(attrs[`gen_ai.request.${reqKey}`]);
    if (n != null && out[name] == null) out[name] = n;
  }
  const blob =
    parseMaybe(attrs["gen_ai.request.parameters"]) ||
    parseMaybe(span?.model_parameters) ||
    parsedRequestBody(span);
  if (blob && typeof blob === "object") {
    for (const [k, v] of Object.entries(blob)) {
      const name = REQUEST_PARAM_KEYS[k];
      const n = toNumber(v);
      if (name && n != null && out[name] == null) out[name] = n;
    }
  }
  return out;
}

export function buildPromptConfigFromSpan(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  const templateVars = parseTemplateVariables(attrs);

  const messages = normalizeMessagesFromSpan(span).map((m) => {
    let text = m.content || "";
    if (templateVars) text = templatizeText(text, templateVars);
    return { role: m.role, content: [{ type: "text", text }] };
  });

  // Guarantee a system slot, by role (not position).
  if (messages.length && !messages.some((m) => m.role === "system")) {
    messages.unshift({ role: "system", content: [{ type: "text", text: "" }] });
  }

  const variableNames = {};
  if (templateVars) {
    for (const [name, value] of Object.entries(templateVars)) {
      variableNames[name] = value != null ? [String(value)] : [];
    }
  }

  return {
    messages,
    variableNames,
    model: extractModelFromSpan(span),
    provider: extractProviderFromSpan(span),
    parameters: extractParamsFromSpan(span),
    responseFormat: extractResponseFormatFromSpan(span),
  };
}
