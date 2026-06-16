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

// Replace resolved variable values in text with {{varName}} placeholders.
// Longer values first so a value that is a substring of another isn't clobbered.
function templatizeText(text, variables) {
  if (!text || !variables) return text;
  let result = text;
  const entries = Object.entries(variables)
    .filter(([, val]) => val != null && String(val).length > 0)
    .sort((a, b) => String(b[1]).length - String(a[1]).length);
  for (const [name, value] of entries) {
    const strVal = String(value);
    let idx = result.indexOf(strVal);
    while (idx !== -1) {
      result =
        result.slice(0, idx) +
        `{{${name}}}` +
        result.slice(idx + strVal.length);
      idx = result.indexOf(strVal, idx + `{{${name}}}`.length);
    }
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

function normalizeMessageObject(msg) {
  if (!msg || typeof msg !== "object") return null;
  const role = msg.role || msg.message?.role;
  let content;
  if (msg.content != null) content = stringifyContent(msg.content);
  else if (Array.isArray(msg.parts)) content = stringifyContent(msg.parts);
  else content = "";
  return role ? { role, content } : null;
}

function fromFlattenedAttrs(attrs) {
  const temp = {};
  for (const key of Object.keys(attrs)) {
    const prefix = INPUT_MESSAGE_PREFIXES.find((p) => key.startsWith(`${p}.`));
    if (!prefix) continue;
    const rest = key.slice(prefix.length + 1);
    const firstDot = rest.indexOf(".");
    if (firstDot === -1) continue;
    const index = rest.slice(0, firstDot);
    const prop = rest.slice(firstDot + 1);
    if (!temp[index])
      temp[index] = { role: undefined, content: null, parts: {} };

    if (prop === "message.role" || prop === "role") {
      temp[index].role = attrs[key];
      continue;
    }
    // nested structured content: (message.)contents.N.message_content.<field>
    const nested = prop.match(
      /^(?:message\.)?contents?\.(\d+)\.message_content\.(\w+)$/,
    );
    if (nested) {
      if (nested[2] === "text") temp[index].parts[nested[1]] = attrs[key];
      continue;
    }
    if (prop === "message.content" || prop === "content") {
      temp[index].content = attrs[key];
    }
  }

  return Object.keys(temp)
    .sort((a, b) => Number(a) - Number(b))
    .map((i) => {
      const m = temp[i];
      const partKeys = Object.keys(m.parts);
      const content = partKeys.length
        ? partKeys
            .sort((a, b) => Number(a) - Number(b))
            .map((k) => m.parts[k])
            .join("\n")
        : stringifyContent(m.content);
      return m.role ? { role: m.role, content } : null;
    })
    .filter(Boolean);
}

function fromArrayAttr(attrs) {
  for (const prefix of INPUT_MESSAGE_PREFIXES) {
    const value = attrs[prefix];
    if (Array.isArray(value))
      return value.map(normalizeMessageObject).filter(Boolean);
  }
  return [];
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
    return data.contents.map(normalizeMessageObject).filter(Boolean);
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
  // Plain text → one user turn (don't dump an unparseable blob — the original bug).
  if (!messages.length && typeof input === "string" && input.trim()) {
    messages = [{ role: "user", content: input }];
  }
  return messages;
}

export function extractModelFromSpan(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  if (span?.model) return span.model;
  for (const key of MODEL_KEYS) if (attrs[key]) return attrs[key];
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

// Whitelisted numeric model params; transport/junk keys are dropped.
export function extractParamsFromSpan(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  const out = {};
  for (const [reqKey, name] of Object.entries(REQUEST_PARAM_KEYS)) {
    const v = attrs[`gen_ai.request.${reqKey}`];
    if (typeof v === "number") out[name] = v;
  }
  const blob =
    parseMaybe(attrs["gen_ai.request.parameters"]) ||
    parseMaybe(span?.model_parameters) ||
    parsedRequestBody(span);
  if (blob && typeof blob === "object") {
    for (const [k, v] of Object.entries(blob)) {
      const name = REQUEST_PARAM_KEYS[k];
      if (name && typeof v === "number" && out[name] == null) out[name] = v;
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
