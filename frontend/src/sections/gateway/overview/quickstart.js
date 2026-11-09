// The Overview's quickstart: one chat request against this gateway in curl,
// Python and TypeScript (OpenAI SDKs). The key is always a placeholder read
// from the environment, never a real key. The gateway's base URL already ends
// in /v1. (The model pages have their own snippets in models/snippets.js.)

import { providerStatusKey } from "../providers/provider-rows";

export const API_KEY_ENV = "AGENTCC_API_KEY";
const PLACEHOLDER_BASE_URL = "https://YOUR_GATEWAY_URL/v1";

export function baseUrlOf(baseUrl) {
  const trimmed = String(baseUrl || "")
    .trim()
    .replace(/\/+$/, "");
  return trimmed || PLACEHOLDER_BASE_URL;
}

export function quickstartSnippets(baseUrl, model) {
  const url = baseUrlOf(baseUrl);
  const name = JSON.stringify(model);
  return [
    {
      key: "curl",
      label: "cURL",
      code: [
        `curl ${url}/chat/completions \\`,
        `  -H "Authorization: Bearer $${API_KEY_ENV}" \\`,
        `  -H "Content-Type: application/json" \\`,
        `  -d '{`,
        `    "model": ${name},`,
        `    "messages": [{"role": "user", "content": "Hello"}]`,
        `  }'`,
      ].join("\n"),
    },
    {
      key: "python",
      label: "Python",
      code: [
        "import os",
        "from openai import OpenAI",
        "",
        "client = OpenAI(",
        `    base_url=${JSON.stringify(url)},`,
        `    api_key=os.environ["${API_KEY_ENV}"],`,
        ")",
        "",
        "response = client.chat.completions.create(",
        `    model=${name},`,
        `    messages=[{"role": "user", "content": "Hello"}],`,
        ")",
        "print(response.choices[0].message.content)",
      ].join("\n"),
    },
    {
      key: "typescript",
      label: "TypeScript",
      code: [
        'import OpenAI from "openai";',
        "",
        "const client = new OpenAI({",
        `  baseURL: ${JSON.stringify(url)},`,
        `  apiKey: process.env.${API_KEY_ENV},`,
        "});",
        "",
        "const response = await client.chat.completions.create({",
        `  model: ${name},`,
        '  messages: [{ role: "user", content: "Hello" }],',
        "});",
        "console.log(response.choices[0].message.content);",
      ].join("\n"),
    },
  ];
}

const NOT_CHAT = /(embed|whisper|tts|dall-e|image|audio|moderation|rerank)/i;
const modelName = (model) =>
  typeof model === "string" ? model : model?.id || model?.name;

// A chat model the organization really has: the first chat-looking model of
// its first provider that lists one (embedding, image and audio models are
// skipped), with that provider's id: { provider, model }, or null.
export function quickstartChoice(providers) {
  for (const provider of Array.isArray(providers) ? providers : []) {
    const models = Array.isArray(provider?.models)
      ? provider.models.map(modelName)
      : [];
    const model = models.find((name) => name && !NOT_CHAT.test(name));
    if (model) {
      return { provider: String(provider?.id || provider?.name || ""), model };
    }
  }
  return null;
}

// The quickstart's model: a chat model of a provider measured Healthy (5 or
// more requests in the last 24 hours, status healthy: provider-rows.js), so
// a copied example does not name a model whose provider fails or has too
// few requests to tell; else a placeholder the reader replaces.
export function quickstartModel(providers) {
  const healthy = (Array.isArray(providers) ? providers : []).filter(
    (provider) => providerStatusKey(provider) === "healthy",
  );
  return quickstartChoice(healthy)?.model || "YOUR_MODEL";
}
