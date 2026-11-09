// Copy-ready requests against this gateway's OpenAI-compatible endpoint. The
// gateway's base URL already ends in /v1. The key is always an environment
// variable, never a real key.

const PLACEHOLDER_BASE_URL = "https://YOUR_GATEWAY_URL/v1";

export const API_KEY_ENV = "AGENTCC_API_KEY";

export function gatewayBaseUrl(baseUrl) {
  const trimmed = String(baseUrl || "")
    .trim()
    .replace(/\/+$/, "");
  return trimmed || PLACEHOLDER_BASE_URL;
}

const q = (text) => JSON.stringify(text);

// ---- Chat ----

export function curlSnippet(baseUrl, model, { stream = false } = {}) {
  return [
    `curl ${stream ? "-N " : ""}${gatewayBaseUrl(baseUrl)}/chat/completions \\`,
    `  -H "Authorization: Bearer $${API_KEY_ENV}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{`,
    `    "model": ${q(model)},`,
    ...(stream ? [`    "stream": true,`] : []),
    `    "messages": [{"role": "user", "content": "Hello"}]`,
    `  }'`,
  ].join("\n");
}

const pythonClient = (baseUrl) => [
  "import os",
  "from openai import OpenAI",
  "",
  "client = OpenAI(",
  `    base_url=${q(gatewayBaseUrl(baseUrl))},`,
  `    api_key=os.environ["${API_KEY_ENV}"],`,
  ")",
  "",
];

export function pythonSnippet(baseUrl, model, { stream = false } = {}) {
  const call = [
    `    model=${q(model)},`,
    `    messages=[{"role": "user", "content": "Hello"}],`,
  ];
  if (stream) {
    return [
      ...pythonClient(baseUrl),
      "stream = client.chat.completions.create(",
      ...call,
      "    stream=True,",
      ")",
      "for chunk in stream:",
      "    if chunk.choices:",
      `        print(chunk.choices[0].delta.content or "", end="", flush=True)`,
      "print()",
    ].join("\n");
  }
  return [
    ...pythonClient(baseUrl),
    "response = client.chat.completions.create(",
    ...call,
    ")",
    "print(response.choices[0].message.content)",
  ].join("\n");
}

const tsClient = (baseUrl) => [
  `import OpenAI from "openai";`,
  "",
  "const client = new OpenAI({",
  `  baseURL: ${q(gatewayBaseUrl(baseUrl))},`,
  `  apiKey: process.env.${API_KEY_ENV},`,
  "});",
  "",
];

export function typescriptSnippet(baseUrl, model, { stream = false } = {}) {
  const call = [
    `  model: ${q(model)},`,
    `  messages: [{ role: "user", content: "Hello" }],`,
  ];
  if (stream) {
    return [
      ...tsClient(baseUrl),
      "const stream = await client.chat.completions.create({",
      ...call,
      "  stream: true,",
      "});",
      "for await (const chunk of stream) {",
      `  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");`,
      "}",
    ].join("\n");
  }
  return [
    ...tsClient(baseUrl),
    "const response = await client.chat.completions.create({",
    ...call,
    "});",
    "console.log(response.choices[0].message.content);",
  ].join("\n");
}

// ---- Embeddings ----

const embeddingsCurl = (baseUrl, model) =>
  [
    `curl ${gatewayBaseUrl(baseUrl)}/embeddings \\`,
    `  -H "Authorization: Bearer $${API_KEY_ENV}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"model": ${q(model)}, "input": "Hello"}'`,
  ].join("\n");

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
    "const response = await client.embeddings.create({",
    `  model: ${q(model)},`,
    `  input: "Hello",`,
    "});",
    "console.log(response.data[0].embedding.length);",
  ].join("\n");

/**
 * The snippets for one model, as [{ key, label, code }], or [] for a type
 * this page has no request shape for (the page then shows only the route).
 */
export function modelSnippets(
  baseUrl,
  model,
  { type = "chat", stream = false } = {},
) {
  if (type === "chat") {
    return [
      {
        key: "curl",
        label: "cURL",
        code: curlSnippet(baseUrl, model, { stream }),
      },
      {
        key: "python",
        label: "Python",
        code: pythonSnippet(baseUrl, model, { stream }),
      },
      {
        key: "typescript",
        label: "TypeScript",
        code: typescriptSnippet(baseUrl, model, { stream }),
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
  return [];
}

// The gateway's own request fields, on a chat request: dimensions for the
// request log in x-agentcc-metadata, and a session id. -i prints the response
// headers, which carry the request id (x-agentcc-request-id), cost
// (x-agentcc-cost) and latency. No models[] fallback chain: this gateway
// answers one with 400 routing_field_not_yet_supported.
export function gatewayExtrasSnippet(baseUrl, model) {
  return [
    `curl -i ${gatewayBaseUrl(baseUrl)}/chat/completions \\`,
    `  -H "Authorization: Bearer $${API_KEY_ENV}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -H 'x-agentcc-metadata: {"feature": "checkout"}' \\`,
    `  -H "x-agentcc-session-id: session-123" \\`,
    `  -d '{`,
    `    "model": ${q(model)},`,
    `    "messages": [{"role": "user", "content": "Hello"}]`,
    `  }'`,
  ].join("\n");
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
        "const response = await client.chat.completions.create(",
        `  { model: "auto", messages: [{ role: "user", content: "Hello" }] },`,
        `  { headers: { "${PREFERENCE_HEADER}": "cost" } },`,
        ");",
        "console.log(response.model, response.choices[0].message.content);",
      ].join("\n"),
    },
  ];
}
