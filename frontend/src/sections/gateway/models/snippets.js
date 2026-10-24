// Copy-ready requests against this gateway's OpenAI-compatible endpoint. The
// gateway's base URL already ends in /v1.

const PLACEHOLDER_BASE_URL = "https://YOUR_GATEWAY_URL/v1";

export const API_KEY_ENV = "AGENTCC_API_KEY";

export function gatewayBaseUrl(baseUrl) {
  const trimmed = String(baseUrl || "")
    .trim()
    .replace(/\/+$/, "");
  return trimmed || PLACEHOLDER_BASE_URL;
}

export function curlSnippet(baseUrl, model) {
  return [
    `curl ${gatewayBaseUrl(baseUrl)}/chat/completions \\`,
    `  -H "Authorization: Bearer $${API_KEY_ENV}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{`,
    `    "model": ${JSON.stringify(model)},`,
    `    "messages": [{"role": "user", "content": "Hello"}]`,
    `  }'`,
  ].join("\n");
}

export function pythonSnippet(baseUrl, model) {
  return [
    "import os",
    "from openai import OpenAI",
    "",
    "client = OpenAI(",
    `    base_url=${JSON.stringify(gatewayBaseUrl(baseUrl))},`,
    `    api_key=os.environ["${API_KEY_ENV}"],`,
    ")",
    "",
    "response = client.chat.completions.create(",
    `    model=${JSON.stringify(model)},`,
    `    messages=[{"role": "user", "content": "Hello"}],`,
    ")",
    "print(response.choices[0].message.content)",
  ].join("\n");
}
