// Copy-only recipes. Configuration, measured provider health and model
// compatibility are separate facts; constructing code never sends a request.
import { providerStatusKey, PROVIDER_STATUS } from "../providers/provider-rows";
import { managedVerdict } from "../models/catalogRows";
import { orgModelPageId } from "../models/orgServed";
import { inAsyncMain } from "../models/snippets";
import { MANAGED_MODE_LABEL } from "../utils/providerLabel";
import { PLACEHOLDER_BASE_URL, trimBaseUrl } from "../utils/snippetBaseUrl";

export const API_KEY_ENV = "AGENTCC_API_KEY";

// Hosted/public versus self-hosted/placeholder policy stays with the caller.
export function baseUrlOf(baseUrl) {
  return trimBaseUrl(baseUrl) || PLACEHOLDER_BASE_URL;
}

const shellQuote = (value) => `'${value.replace(/'/g, "'\\''")}'`;

export function quickstartSnippets(baseUrl, model) {
  const url = baseUrlOf(baseUrl);
  const name = JSON.stringify(model);
  const body = [
    "{",
    `    "model": ${name},`,
    '    "messages": [{"role": "user", "content": "Hello"}]',
    "  }",
  ].join("\n");
  return [
    {
      key: "curl",
      label: "cURL",
      code: [
        `curl ${shellQuote(`${url}/chat/completions`)} \\`,
        `  -H "Authorization: Bearer $${API_KEY_ENV}" \\`,
        '  -H "Content-Type: application/json" \\',
        `  -d ${shellQuote(body)}`,
      ].join("\n"),
    },
    {
      key: "python",
      label: "Python",
      code: [
        "# Install: python -m pip install openai",
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
        '    messages=[{"role": "user", "content": "Hello"}],',
        ")",
        "print(response.choices[0].message.content)",
      ].join("\n"),
    },
    {
      key: "typescript",
      label: "TypeScript",
      code: [
        "// Install: npm install openai",
        'import OpenAI from "openai";',
        "",
        "const client = new OpenAI({",
        `  baseURL: ${JSON.stringify(url)},`,
        `  apiKey: process.env.${API_KEY_ENV},`,
        "});",
        "",
        ...inAsyncMain([
          "const response = await client.chat.completions.create({",
          `  model: ${name},`,
          '  messages: [{ role: "user", content: "Hello" }],',
          "});",
          "console.log(response.choices[0].message.content);",
        ]),
      ].join("\n"),
    },
  ];
}

export function discoverySnippets(baseUrl) {
  const url = baseUrlOf(baseUrl);
  return [
    {
      key: "curl",
      label: "cURL",
      code: [
        `# First set ${API_KEY_ENV} to your gateway API key:`,
        `# export ${API_KEY_ENV}="YOUR_GATEWAY_API_KEY"`,
        `curl ${shellQuote(`${url}/models`)} \\`,
        `  -H "Authorization: Bearer $${API_KEY_ENV}"`,
      ].join("\n"),
    },
    {
      key: "python",
      label: "Python",
      code: [
        "# Install: python -m pip install openai",
        `# Set ${API_KEY_ENV} to your gateway API key before running.`,
        "import os",
        "from openai import OpenAI",
        "",
        "client = OpenAI(",
        `    base_url=${JSON.stringify(url)},`,
        `    api_key=os.environ["${API_KEY_ENV}"],`,
        ")",
        "for model in client.models.list():",
        "    print(model.id)",
      ].join("\n"),
    },
    {
      key: "typescript",
      label: "TypeScript",
      code: [
        "// Install: npm install openai",
        `// Set ${API_KEY_ENV} to your gateway API key before running.`,
        'import OpenAI from "openai";',
        "",
        "const client = new OpenAI({",
        `  baseURL: ${JSON.stringify(url)},`,
        `  apiKey: process.env.${API_KEY_ENV},`,
        "});",
        "",
        ...inAsyncMain([
          "for await (const model of client.models.list()) {",
          "  console.log(model.id);",
          "}",
        ]),
      ].join("\n"),
    },
  ];
}

const modelName = (model) =>
  typeof model === "string" ? model : model?.id || model?.name;

/**
 * BP-09 contract: pure, preferred-first configured routes with exact `id` for
 * requests and model pages, original `provider`/`model`, health/healthLabel,
 * readiness/readinessLabel and compatibility (unverified | reported-chat).
 * This does NOT prove that a particular gateway key's allowlists permit a
 * route. Consumers must check those separately. No managed default is guessed.
 */
export function configuredRouteCandidates(providers) {
  const list = Array.isArray(providers) ? providers : [];
  // orgModelPageId consumes string model IDs, not optional metadata objects.
  const normalized = list.map((provider) => ({
    ...provider,
    models: (Array.isArray(provider?.models) ? provider.models : [])
      .map(modelName).filter((name) => typeof name === "string" && name),
  }));
  const seen = new Set();
  const candidates = list.flatMap((provider) => {
    const health = providerStatusKey(provider);
    if (provider?.pushed !== true || provider.healthy === false ||
        ["unhealthy", "degraded", "not_pushed"].includes(health)) return [];
    const providerId = String(provider.id || provider.name || "");
    return (Array.isArray(provider.models) ? provider.models : []).flatMap((model) => {
      const name = modelName(model);
      const type = typeof model === "object" && model?.type;
      if (type && type !== "chat" && type !== "unknown") return [];
      const id = orgModelPageId(normalized, providerId, name);
      if (!id || seen.has(id)) return [];
      seen.add(id);
      return [{
        id, provider: providerId, model: name,
        health, healthLabel: PROVIDER_STATUS[health].label,
        readiness: "configured",
        readinessLabel: health === "healthy" ? "Configured; provider measured healthy" : "Configured; health is unmeasured",
        compatibility: type === "chat" ? "reported-chat" : "unverified",
      }];
    });
  });
  return candidates.sort((a, b) => Number(b.health === "healthy") - Number(a.health === "healthy"));
}

/**
 * The managed catalog's models that Future AGI credits can pay for (chat,
 * priced, not retired or withheld: catalogRows.js managedVerdict), as request
 * candidates in catalog order. `id` is the catalog model id the request names
 * and `provider` the id a key's provider list must allow. Call only when
 * credits are ready (setupReadiness' `managed`); nothing is preselected.
 */
export function creditRouteCandidates(models) {
  const seen = new Set();
  return (Array.isArray(models) ? models : []).flatMap((model) => {
    const id = typeof model?.id === "string" ? model.id : "";
    if (!id || seen.has(id) || !managedVerdict(model).sold) return [];
    seen.add(id);
    return [{
      id, provider: String(model.provider || ""), model: id,
      billing: "managed",
      healthLabel: MANAGED_MODE_LABEL,
      readiness: "credits",
      readinessLabel: `Runs on ${MANAGED_MODE_LABEL}`,
      compatibility: "reported-chat",
    }];
  });
}

export function quickstartChoice(providers) {
  return configuredRouteCandidates(providers)[0] || null;
}

export function quickstartModel(providers) {
  return quickstartChoice(providers)?.id || null;
}
