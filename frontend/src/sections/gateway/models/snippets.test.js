import { describe, expect, it } from "vitest";

import {
  API_KEY_ENV,
  autoSnippets,
  curlSnippet,
  gatewayExtrasSnippet,
  modelSnippets,
  pythonSnippet,
  typescriptSnippet,
} from "./snippets";

const BASE = "http://localhost:28490/v1";
const MODEL = "anthropic/claude-haiku-5.5";

describe("API snippets", () => {
  it("never carry a real key: the key is an environment variable", () => {
    expect(API_KEY_ENV).toBe("AGENTCC_API_KEY");
    for (const s of modelSnippets(BASE, MODEL, { type: "chat" })) {
      expect(s.code).not.toMatch(/sk-|Bearer [A-Za-z0-9]{12}/);
    }
  });

  it("call chat completions with the exact model id", () => {
    expect(curlSnippet(BASE, MODEL)).toBe(
      [
        `curl ${BASE}/chat/completions \\`,
        `  -H "Authorization: Bearer $AGENTCC_API_KEY" \\`,
        `  -H "Content-Type: application/json" \\`,
        `  -d '{`,
        `    "model": "${MODEL}",`,
        `    "messages": [{"role": "user", "content": "Hello"}]`,
        `  }'`,
      ].join("\n"),
    );
    expect(pythonSnippet(BASE, MODEL)).toContain(`base_url="${BASE}"`);
    expect(pythonSnippet(BASE, MODEL)).toContain(`model="${MODEL}"`);
  });

  it("stream when asked", () => {
    const curl = curlSnippet(BASE, MODEL, { stream: true });
    expect(curl.startsWith(`curl -N ${BASE}/chat/completions`)).toBe(true);
    expect(curl).toContain(`"stream": true`);
    const py = pythonSnippet(BASE, MODEL, { stream: true });
    expect(py).toContain("stream=True");
    expect(py).toContain("if chunk.choices:");
    const ts = typescriptSnippet(BASE, MODEL, { stream: true });
    expect(ts).toContain("stream: true");
    expect(ts).toContain("for await (const chunk of stream)");
  });

  it("use the OpenAI TypeScript SDK pointed at the gateway", () => {
    expect(typescriptSnippet(BASE, MODEL)).toBe(
      [
        `import OpenAI from "openai";`,
        "",
        "const client = new OpenAI({",
        `  baseURL: "${BASE}",`,
        "  apiKey: process.env.AGENTCC_API_KEY,",
        "});",
        "",
        "const response = await client.chat.completions.create({",
        `  model: "${MODEL}",`,
        `  messages: [{ role: "user", content: "Hello" }],`,
        "});",
        "console.log(response.choices[0].message.content);",
      ].join("\n"),
    );
  });

  it("call the embeddings route for an embeddings model", () => {
    const [curl, py, ts] = modelSnippets(BASE, "text-embedding-3-small", {
      type: "embeddings",
    });
    expect(curl.code).toContain(`${BASE}/embeddings`);
    expect(curl.code).toContain(`"input": "Hello"`);
    expect(py.code).toContain("client.embeddings.create(");
    expect(ts.code).toContain("client.embeddings.create({");
  });

  it("offer no snippet for a type it has no request shape for", () => {
    expect(
      modelSnippets(BASE, "black-forest-labs/flux-3-image", { type: "images" }),
    ).toEqual([]);
  });

  it("show the gateway's own fields: metadata and session", () => {
    const code = gatewayExtrasSnippet(BASE, MODEL, {
      fallback: "openai/gpt-4o-mini",
    });
    expect(code).toContain(`-H 'x-agentcc-metadata: {"feature": "checkout"}'`);
    expect(code).toContain(`-H "x-agentcc-session-id: session-123"`);
    expect(code).toContain(`"model": "${MODEL}"`);
    // This gateway answers a body models[] fallback chain with 400
    // routing_field_not_yet_supported (run against the demo gateway,
    // 2026-10-20), so the example must not offer one.
    expect(code).not.toMatch(/"models"/);
    // -i prints the response headers that carry the request id and cost.
    expect(code.split("\n")[0]).toBe(`curl -i ${BASE}/chat/completions \\`);
  });

  it("call model auto with the per-request preference header", () => {
    const [curl, py, ts] = autoSnippets(BASE);
    expect(curl.code).toContain(`"model": "auto"`);
    expect(curl.code).toContain(`-H "x-agentcc-auto-preference: cost"`);
    expect(py.code).toContain(`model="auto"`);
    expect(py.code).toContain(
      `extra_headers={"x-agentcc-auto-preference": "cost"}`,
    );
    expect(ts.code).toContain(`model: "auto"`);
    expect(ts.code).toContain(
      `{ headers: { "x-agentcc-auto-preference": "cost" } }`,
    );
  });

  it("use a placeholder base URL when the gateway URL is unknown", () => {
    expect(typescriptSnippet("", MODEL)).toContain(
      `baseURL: "https://YOUR_GATEWAY_URL/v1"`,
    );
  });
});
