// Copy-ready requests against this gateway. Accept a root or /v1 base URL,
// retaining any deployment path prefix. The key is always an environment
// variable, never a real key. Which base URL a page passes in (the configured
// public URL on a hosted deployment, the placeholder on a self-hosted one) is
// decided by snippetBaseUrl (../utils/snippetBaseUrl.js).

import { PLACEHOLDER_BASE_URL, trimBaseUrl } from "../utils/snippetBaseUrl";
import { buildChatRequestSnapshot } from "./requestSnapshot";

export const API_KEY_ENV = "AGENTCC_API_KEY";

export function gatewayBaseUrl(baseUrl) {
  const base = trimBaseUrl(baseUrl) || PLACEHOLDER_BASE_URL;
  return base.endsWith("/v1") ? base : `${base}/v1`;
}

const q = (text) => JSON.stringify(text);
const shellQuote = (text) => `'${text.replace(/'/g, "'\\''")}'`;
const shellUrl = (url) => /^[a-zA-Z0-9:/._-]+$/.test(url) ? url : shellQuote(url);
const jsonValue = (value) => {
  if (Array.isArray(value)) return `[${value.map(jsonValue).join(", ")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value).map(([key, item]) => `${q(key)}: ${jsonValue(item)}`).join(", ")}}`;
  }
  return q(value);
};

// JSON strings are valid Python strings; booleans/null must be Python literals,
// including inside nested message content (never replace words inside text).
const pythonValue = (value) => {
  if (value === true) return "True";
  if (value === false) return "False";
  if (value === null) return "None";
  if (Array.isArray(value)) return `[${value.map(pythonValue).join(", ")}]`;
  if (typeof value === "object") {
    return `{${Object.entries(value).map(([key, item]) => `${q(key)}: ${pythonValue(item)}`).join(", ")}}`;
  }
  return q(value);
};

function chatBody(model, { stream = false, request } = {}) {
  // Never carry a previous model's prompt into a different model's example.
  const body = request?.model === model && Array.isArray(request.messages)
    ? buildChatRequestSnapshot(request)
    : { model, messages: [{ role: "user", content: "Hello" }], ...(stream ? { stream: true } : {}) };
  if (body.stream) body.stream_options = { include_usage: true };
  return body;
}

function curlRequest(baseUrl, endpoint, body, { headers = [], showHeaders = false, output } = {}) {
  const data = `{\n${Object.entries(body).map(([key, value]) => `    ${q(key)}: ${jsonValue(value)}`).join(",\n")}\n  }`;
  return [
    `curl ${showHeaders ? "-i " : ""}${body.stream ? "-N " : ""}${shellUrl(`${gatewayBaseUrl(baseUrl)}${endpoint}`)} \\`,
    `  -H "Authorization: Bearer $${API_KEY_ENV}" \\`,
    `  -H "Content-Type: application/json" \\`,
    ...headers.map((header) => `  -H ${header} \\`),
    ...(output ? [`  --output ${shellUrl(output)} \\`] : []),
    `  -d ${shellQuote(data)}`,
  ].join("\n");
}

// ---- Chat ----

export function curlSnippet(baseUrl, model, options = {}) {
  return curlRequest(baseUrl, "/chat/completions", { ...chatBody(model, options), ...options.routing });
}

const pythonClient = (baseUrl, stream = false, noRetries = stream) => [
  "import os",
  ...(stream ? ["import sys"] : []),
  "from openai import OpenAI",
  "",
  "client = OpenAI(",
  `    base_url=${q(gatewayBaseUrl(baseUrl))},`,
  `    api_key=os.environ["${API_KEY_ENV}"],`,
  ...(noRetries ? ["    max_retries=0,"] : []),
  ")",
  "",
];

export function pythonSnippet(baseUrl, model, options = {}) {
  const body = chatBody(model, options);
  const call = [
    ...Object.entries(body).map(([key, value]) => `    ${key}=${pythonValue(value)},`),
    // OpenAI's Python SDK merges extra_body into the top-level wire body.
    ...(options.routing ? [`    extra_body=${pythonValue(options.routing)},`] : []),
  ];
  if (body.stream) {
    return [
      ...pythonClient(baseUrl, true),
      "try:",
      "    stream = client.chat.completions.create(",
      ...call.map((line) => `    ${line}`),
      "    )",
      "    for chunk in stream:",
      '        if getattr(chunk, "error", None):',
      "            raise RuntimeError(chunk.error)",
      "        if chunk.choices:",
      '            print(chunk.choices[0].delta.content or "", end="", flush=True)',
      "        if chunk.usage:",
      '            print("\\nUsage:", chunk.usage, file=sys.stderr)',
      "    print()",
      "except Exception as error:",
      '    print("Stream failed; partial output is incomplete.", error, file=sys.stderr)',
      "    raise  # Do not automatically replay partial output.",
    ].join("\n");
  }
  return [
    ...pythonClient(baseUrl, false, Boolean(options.routing)),
    "response = client.chat.completions.create(",
    ...call,
    ")",
    "print(response.choices[0].message.content)",
  ].join("\n");
}

const tsClient = (baseUrl, noRetries = false) => [
  `import OpenAI from "openai";`,
  "",
  "const client = new OpenAI({",
  `  baseURL: ${q(gatewayBaseUrl(baseUrl))},`,
  `  apiKey: process.env.${API_KEY_ENV},`,
  ...(noRetries ? ["  maxRetries: 0,"] : []),
  "});",
  "",
];

// A copied .ts file runs as CommonJS in a default Node project (npm init -y),
// where top-level await is a syntax error. Run the awaited lines inside
// main() and report a failure with a non-zero exit code.
export const inAsyncMain = (lines) => [
  "async function main() {",
  ...lines.join("\n").split("\n").map((line) => (line ? `  ${line}` : line)),
  "}",
  "",
  "main().catch((error) => {",
  "  console.error(error);",
  "  process.exitCode = 1;",
  "});",
];

export function typescriptSnippet(baseUrl, model, options = {}) {
  const body = chatBody(model, options);
  const routing = options.routing
    ? [`const routing = ${jsonValue(options.routing)};`, ""] : [];
  const call = [
    ...Object.entries(body).map(([key, value]) =>
      `  ${key}: ${key === "messages" && !options.request ? '[{ role: "user", content: "Hello" }]' : jsonValue(value)},`,
    ),
    // Spread custom top-level fields without SDK excess-property assertions.
    ...(options.routing ? ["  ...routing,"] : []),
  ];
  if (body.stream) {
    return [
      ...tsClient(baseUrl, true),
      ...routing,
      ...inAsyncMain([
        "try {",
        "  const stream = await client.chat.completions.create({",
        ...call.map((line) => `  ${line}`),
        "  });",
        "  for await (const chunk of stream) {",
        '    if ("error" in chunk) throw new Error(JSON.stringify(chunk.error));',
        '    process.stdout.write(chunk.choices[0]?.delta?.content ?? "");',
        '    if (chunk.usage) console.error("Usage:", chunk.usage);',
        "  }",
        "} catch (error) {",
        '  console.error("Stream failed; partial output is incomplete.", error);',
        "  throw error; // Do not automatically replay partial output.",
        "}",
      ]),
    ].join("\n");
  }
  return [
    ...tsClient(baseUrl, Boolean(options.routing)),
    ...routing,
    ...inAsyncMain([
      "const response = await client.chat.completions.create({",
      ...call,
      "});",
      "console.log(response.choices[0].message.content);",
    ]),
  ].join("\n");
}

// ---- Caller routing (chat only; not an organization configuration change) ----

export function routingSnippets(baseUrl, model, {
  models = ["YOUR_PROVIDER_ID/YOUR_FALLBACK_MODEL"],
  provider = {
    order: ["YOUR_UPSTREAM_PROVIDER_SLUG"],
    only: ["YOUR_UPSTREAM_PROVIDER_SLUG"],
    allow_fallbacks: false,
  },
  stream = false,
  request,
} = {}) {
  // Match the caller-chain bound without introducing a routing editor.
  const chain = [...new Set(models.filter((id) => typeof id === "string")
    .map((id) => id.trim()).filter(Boolean))].slice(0, 5);
  const options = { stream, request, routing: { models: chain, provider } };
  return [
    { key: "curl", label: "cURL", code: curlSnippet(baseUrl, model, options) },
    { key: "python", label: "Python", code: pythonSnippet(baseUrl, model, options) },
    { key: "typescript", label: "TypeScript", code: typescriptSnippet(baseUrl, model, options) },
  ];
}

// ---- Simple-text native APIs (instructional, never a compatibility verdict) ----

export function nativeSnippets(baseUrl, model, { api = "messages", text = "Hello", maxTokens = 256 } = {}) {
  if (!["messages", "responses"].includes(api)) return [];
  const messages = api === "messages";
  // The Messages SDK appends /v1/messages; OpenAI appends /responses.
  const sdkBase = messages ? gatewayBaseUrl(baseUrl).replace(/\/v1$/, "") : gatewayBaseUrl(baseUrl);
  const client = messages ? "Anthropic" : "OpenAI";
  const body = messages
    ? { model, max_tokens: maxTokens, messages: [{ role: "user", content: text }] }
    : { model, input: text };
  return [
    {
      key: "curl",
      label: "cURL",
      code: curlRequest(baseUrl, `/${api}`, body, {
        headers: messages ? ["'anthropic-version: 2023-06-01'"] : [],
      }),
    },
    {
      key: "python",
      label: "Python",
      code: [
        "import os",
        `from ${messages ? "anthropic" : "openai"} import ${client}`,
        "",
        `client = ${client}(`,
        `    base_url=${q(sdkBase)},`,
        `    api_key=os.environ["${API_KEY_ENV}"],`,
        "    max_retries=0,",
        ")",
        "",
        `response = client.${api}.create(`,
        ...Object.entries(body).map(([key, value]) => `    ${key}=${pythonValue(value)},`),
        ")",
        ...(messages ? [
          "for block in response.content:",
          '    if block.type == "text":',
          "        print(block.text)",
        ] : ["print(response.output_text)"]),
      ].join("\n"),
    },
    {
      key: "typescript",
      label: "TypeScript",
      code: [
        `import ${client} from "${messages ? "@anthropic-ai/sdk" : "openai"}";`,
        "",
        `const client = new ${client}({`,
        `  baseURL: ${q(sdkBase)},`,
        `  apiKey: process.env.${API_KEY_ENV},`,
        "  maxRetries: 0,",
        "});",
        "",
        ...inAsyncMain([
          `const response = await client.${api}.create({`,
          ...Object.entries(body).map(([key, value]) => `  ${key}: ${jsonValue(value)},`),
          "});",
          ...(messages ? [
            "for (const block of response.content) {",
            '  if (block.type === "text") console.log(block.text);',
            "}",
          ] : ["console.log(response.output_text);"]),
        ]),
      ].join("\n"),
    },
  ];
}

// ---- Embeddings ----

const embeddingsCurl = (baseUrl, model) =>
  curlRequest(baseUrl, "/embeddings", { model, input: "Hello" });

const embeddingsPython = (baseUrl, model) =>
  [
    ...pythonClient(baseUrl),
    "response = client.embeddings.create(",
    `    model=${q(model)},`,
    `    input="Hello",`,
    ")",
    "print(len(response.data[0].embedding))",
  ].join("\n");

const embeddingsTypescript = (baseUrl, model) =>
  [
    ...tsClient(baseUrl),
    ...inAsyncMain([
      "const response = await client.embeddings.create({",
      `  model: ${q(model)},`,
      `  input: "Hello",`,
      "});",
      "console.log(response.data[0].embedding.length);",
    ]),
  ].join("\n");

/**
 * The snippets for one model, as [{ key, label, code }], or [] for a type
 * this page has no complete request shape for. These never send a request.
 */
export function modelSnippets(
  baseUrl,
  model,
  { type = "chat", stream = false, request } = {},
) {
  if (type === "chat") {
    return [
      {
        key: "curl",
        label: "cURL",
        code: curlSnippet(baseUrl, model, { stream, request }),
      },
      {
        key: "python",
        label: "Python",
        code: pythonSnippet(baseUrl, model, { stream, request }),
      },
      {
        key: "typescript",
        label: "TypeScript",
        code: typescriptSnippet(baseUrl, model, { stream, request }),
      },
    ];
  }
  if (type === "embeddings") {
    return [
      { key: "curl", label: "cURL", code: embeddingsCurl(baseUrl, model) },
      {
        key: "python",
        label: "Python",
        code: embeddingsPython(baseUrl, model),
      },
      {
        key: "typescript",
        label: "TypeScript",
        code: embeddingsTypescript(baseUrl, model),
      },
    ];
  }
  if (type === "images" || type === "rerank") {
    const endpoint = type === "images" ? "/images/generations" : "/rerank";
    const body = type === "images"
      ? { model, prompt: "A watercolor landscape at sunrise" }
      : {
          model,
          query: "What is an API gateway?",
          documents: [
            "An API gateway routes requests to services.",
            "A garden needs regular watering.",
          ],
        };
    return [{ key: "curl", label: "cURL", code: curlRequest(baseUrl, endpoint, body) }];
  }
  if (type === "audio_speech") {
    return [{
      key: "curl",
      label: "cURL",
      code: curlRequest(baseUrl, "/audio/speech", {
        model, input: "Hello", voice: "YOUR_VOICE", response_format: "mp3",
      }, { output: "speech.mp3" }),
    }];
  }
  if (type === "audio_transcriptions") {
    return [{
      key: "curl",
      label: "cURL",
      code: [
        `curl ${shellUrl(`${gatewayBaseUrl(baseUrl)}/audio/transcriptions`)} \\`,
        `  -H "Authorization: Bearer $${API_KEY_ENV}" \\`,
        `  -F ${shellQuote(`model=${model}`)} \\`,
        "  -F 'file=@./YOUR_AUDIO_FILE.wav' \\",
        "  -F 'response_format=text'",
      ].join("\n"),
    }];
  }
  return [];
}

// The gateway's own request fields, on a chat request: dimensions for the
// request log in x-agentcc-metadata, and a session id. -i prints the response
// headers, which carry the request id (x-agentcc-request-id), cost
// (x-agentcc-cost) and latency. Caller models[] chains are offered separately
// by routingSnippets; the explicit organization fallback switch still applies.
export function gatewayExtrasSnippet(baseUrl, model, options = {}) {
  return curlRequest(baseUrl, "/chat/completions", chatBody(model, options), {
    showHeaders: true,
    headers: [
      `'x-agentcc-metadata: {"feature": "checkout"}'`,
      '"x-agentcc-session-id: session-123"',
    ],
  });
}

// ---- model "auto" ----

const PREFERENCE_HEADER = "x-agentcc-auto-preference";

/** Requests for model "auto", with the per-request preference override. */
export function autoSnippets(baseUrl) {
  const base = gatewayBaseUrl(baseUrl);
  return [
    {
      key: "curl",
      label: "cURL",
      code: [
        `curl ${base}/chat/completions \\`,
        `  -H "Authorization: Bearer $${API_KEY_ENV}" \\`,
        `  -H "Content-Type: application/json" \\`,
        `  -H "${PREFERENCE_HEADER}: cost" \\`,
        `  -d '{`,
        `    "model": "auto",`,
        `    "messages": [{"role": "user", "content": "Hello"}]`,
        `  }'`,
      ].join("\n"),
    },
    {
      key: "python",
      label: "Python",
      code: [
        ...pythonClient(baseUrl),
        "response = client.chat.completions.create(",
        `    model="auto",`,
        `    messages=[{"role": "user", "content": "Hello"}],`,
        `    extra_headers={"${PREFERENCE_HEADER}": "cost"},`,
        ")",
        "print(response.model, response.choices[0].message.content)",
      ].join("\n"),
    },
    {
      key: "typescript",
      label: "TypeScript",
      code: [
        ...tsClient(baseUrl),
        ...inAsyncMain([
          "const response = await client.chat.completions.create(",
          `  { model: "auto", messages: [{ role: "user", content: "Hello" }] },`,
          `  { headers: { "${PREFERENCE_HEADER}": "cost" } },`,
          ");",
          "console.log(response.model, response.choices[0].message.content);",
        ]),
      ].join("\n"),
    },
  ];
}
