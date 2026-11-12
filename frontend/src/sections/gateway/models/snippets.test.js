import { execFileSync } from "node:child_process";
import { describe, expect, it, vi } from "vitest";

import {
  API_KEY_ENV,
  autoSnippets,
  curlSnippet,
  gatewayExtrasSnippet,
  modelSnippets,
  nativeSnippets,
  routingSnippets,
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
        "async function main() {",
        "  const response = await client.chat.completions.create({",
        `    model: "${MODEL}",`,
        `    messages: [{ role: "user", content: "Hello" }],`,
        "  });",
        "  console.log(response.choices[0].message.content);",
        "}",
        "",
        "main().catch((error) => {",
        "  console.error(error);",
        "  process.exitCode = 1;",
        "});",
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
      modelSnippets(BASE, "example/unsupported-video", { type: "video" }),
    ).toEqual([]);
  });

  it("show the gateway's own fields: metadata and session", () => {
    const code = gatewayExtrasSnippet(BASE, MODEL, {
      fallback: "openai/gpt-4o-mini",
    });
    expect(code).toContain(`-H 'x-agentcc-metadata: {"feature": "checkout"}'`);
    expect(code).toContain(`-H "x-agentcc-session-id: session-123"`);
    expect(code).toContain(`"model": "${MODEL}"`);
    // Headers remain independent of the opt-in caller routing recipe.
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

// Run the generated shell with a function in place of curl: no network call.
function curlArgs(code) {
  return execFileSync("/bin/sh", [
    "-c", `curl() { printf '%s\\0' "$@"; };\n${code}`,
  ], { encoding: "utf8" }).split("\0");
}

function curlBody(code) {
  const args = curlArgs(code);
  return JSON.parse(args[args.indexOf("-d") + 1]);
}

const submitted = (stream) => ({
  model: "example-provider/team's-model",
  messages: [
    { role: "system", content: 'Say "hello".\nDon\'t omit \\ paths or 雪.' },
    { role: "user", content: 'First line\nSecond "quoted" line with $HOME and `text`.' },
  ],
  temperature: 0,
  max_tokens: 123,
  top_p: 0.75,
  stream,
});

async function runTypescript(code, chunks = []) {
  const create = vi.fn(async (body) => body.stream ? (async function* () {
    for (const chunk of chunks) yield chunk;
  })() : { choices: [{ message: { content: "answer" } }] });
  const output = { log: vi.fn(), error: vi.fn() };
  const process = { env: { AGENTCC_API_KEY: "synthetic-test-key" }, stdout: { write: vi.fn() } };
  const OpenAI = class {
    constructor() { this.chat = { completions: { create } }; }
  };
  // An example that ends by calling main() returns that call here instead, so
  // a failure reaches this harness rather than the example's own handler.
  const body = code
    .replace(/^import OpenAI[^\n]*\n/, "")
    .replace(/\nmain\(\)\.catch\([\s\S]*$/, "\nreturn main();");
  const execute = new (Object.getPrototypeOf(async function () {}).constructor)(
    "OpenAI", "console", "process", body,
  );
  let error;
  try { await execute(OpenAI, output, process); } catch (caught) { error = caught; }
  return { create, output, process, error };
}

// Execute Python against an in-memory SDK stub, not an installed SDK/provider.
function runPython(code, lateError = false) {
  const harness = `import json, os, sys, types
os.environ["AGENTCC_API_KEY"] = "synthetic-test-key"
calls = []
class FakeClient:
    def __init__(self, **kwargs):
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))
    def create(self, **kwargs):
        calls.append(kwargs)
        if not kwargs.get("stream"):
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="answer"))])
        def chunks():
            yield types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content="partial"))], usage=None)
            yield types.SimpleNamespace(choices=[], usage={"total_tokens": 4})
            if ${lateError ? "True" : "False"}:
                raise RuntimeError("late upstream error")
        return chunks()
sys.modules["openai"] = types.SimpleNamespace(OpenAI=FakeClient)
error = None
try:
    exec(${JSON.stringify(code)})
except Exception as caught:
    error = str(caught)
print("\\nRESULT=" + json.dumps({"calls": calls, "error": error}))
`;
  const output = execFileSync("python3", ["-I", "-c", harness], { encoding: "utf8" });
  return JSON.parse(output.split("RESULT=")[1]);
}

describe("submitted request export", () => {
  it.each([false, true])("preserves quoted multiline values in all languages (stream=%s)", async (stream) => {
    const request = submitted(stream);
    const expected = { ...request, ...(stream ? { stream_options: { include_usage: true } } : {}) };
    const [curl, python, typescript] = modelSnippets(BASE, request.model, { request });
    expect(curlBody(curl.code)).toEqual(expected);
    expect(curlBody(gatewayExtrasSnippet(BASE, request.model, { request }))).toEqual(expected);
    const py = runPython(python.code);
    expect(py.error).toBeNull();
    expect(py.calls).toEqual([expected]);
    const ts = await runTypescript(typescript.code);
    expect(ts.error).toBeUndefined();
    expect(ts.create).toHaveBeenCalledTimes(1);
    expect(ts.create).toHaveBeenCalledWith(expected);
    for (const { code } of [curl, python, typescript]) {
      expect(code).not.toMatch(/admin|temporary_key|test-api-key/);
      expect(code).toContain(API_KEY_ENV);
    }
  });

  it("does not apply a different model's snapshot", () => {
    const body = curlBody(curlSnippet(BASE, MODEL, { request: submitted(true) }));
    expect(body).toEqual({ model: MODEL, messages: [{ role: "user", content: "Hello" }] });
    expect(pythonSnippet(BASE, MODEL, { request: submitted(true) })).not.toContain("max_tokens");
    expect(typescriptSnippet(BASE, MODEL, { request: submitted(true) })).not.toContain("top_p");
  });

  it("requests final usage and surfaces late stream errors without replay", async () => {
    const request = submitted(true);
    const [curl, python, typescript] = modelSnippets(BASE, request.model, { request });
    expect(curl.code).toMatch(/^curl -N /);
    expect(curlBody(curl.code).stream_options).toEqual({ include_usage: true });
    const ts = await runTypescript(typescript.code, [
      { choices: [{ delta: { content: "partial" } }] },
      { choices: [], usage: { total_tokens: 4 } },
      { error: { message: "late upstream error" } },
    ]);
    expect(ts.process.stdout.write).toHaveBeenCalledWith("partial");
    expect(ts.output.error).toHaveBeenCalledWith("Usage:", { total_tokens: 4 });
    expect(ts.error?.message).toContain("late upstream error");
    expect(ts.output.error).toHaveBeenCalledWith(expect.stringMatching(/partial output/i), expect.any(Error));
    expect(ts.create).toHaveBeenCalledTimes(1);
    const py = runPython(python.code, true);
    expect(py.error).toBe("late upstream error");
    expect(py.calls).toHaveLength(1);
    expect(python.code).toContain("max_retries=0");
    expect(typescript.code).toContain("maxRetries: 0");
  });

  it.each([
    ["images", "/images/generations", { prompt: "A watercolor landscape at sunrise" }],
    ["rerank", "/rerank", { query: "What is an API gateway?", documents: ["An API gateway routes requests to services.", "A garden needs regular watering."] }],
  ])("provides the required %s fields without chat or streaming fields", (type, path, fields) => {
    const snippets = modelSnippets(BASE, MODEL, { type, stream: true, request: submitted(true) });
    expect(snippets).toHaveLength(1);
    expect(snippets[0].code).toContain(`${BASE}${path}`);
    expect(curlBody(snippets[0].code)).toEqual({ model: MODEL, ...fields });
    expect(snippets[0].code).not.toContain("messages");
    expect(snippets[0].code).not.toContain("-N");
  });

  it("also quotes provider-qualified IDs safely outside chat", () => {
    const model = "example-provider/team's-model";
    const [embedding] = modelSnippets(BASE, model, { type: "embeddings" });
    expect(curlBody(embedding.code)).toEqual({ model, input: "Hello" });
    expect(curlBody(gatewayExtrasSnippet(BASE, model)).model).toBe(model);
  });
});

describe("native API instructional recipes", () => {
  const root = "https://gateway.example.test/team/proxy";
  const model = "own-provider/team's-model";
  const text = 'Say "hello".\nDon\'t omit \\ paths, $HOME or 雪.';

  it.each(["", "/", "/v1", "/v1/"])("keeps one version segment for Messages and Responses from %s", (suffix) => {
    for (const api of ["messages", "responses"]) {
      const [curl, python, typescript] = nativeSnippets(`${root}${suffix}`, model, { api, text, maxTokens: 123 });
      const expected = api === "messages"
        ? { model, max_tokens: 123, messages: [{ role: "user", content: text }] }
        : { model, input: text };
      const endpoint = `${root}/v1/${api}`;
      const args = curlArgs(curl.code);
      expect(args.filter((arg) => arg === endpoint)).toHaveLength(1);
      expect(curlBody(curl.code)).toEqual(expected);
      const sdkBase = api === "messages" ? root : `${root}/v1`;
      expect(python.code).toContain(`base_url=${JSON.stringify(sdkBase)}`);
      expect(typescript.code).toContain(`baseURL: ${JSON.stringify(sdkBase)}`);
      expect(python.code).toContain(`client.${api}.create(`);
      expect(typescript.code).toContain(`client.${api}.create({`);
      // Inspect generated SDK source only: no SDK is installed or executed here.
      for (const { code } of [python, typescript]) {
        expect(code).toContain(JSON.stringify(model));
        expect(code).toContain(JSON.stringify(text));
        expect(code).not.toContain("/v1/v1");
        expect(code).not.toMatch(/chat\.completions|stream[=:]/);
      }
      if (api === "messages") {
        expect(args).toContain("anthropic-version: 2023-06-01");
        expect(python.code).toContain("max_tokens=123");
        expect(typescript.code).toContain("max_tokens: 123");
        expect(python.code).toContain("block.text");
        expect(typescript.code).toContain("block.text");
      } else {
        expect(python.code).toContain(`input=${JSON.stringify(text)}`);
        expect(typescript.code).toContain(`input: ${JSON.stringify(text)}`);
        expect(python.code).toContain("response.output_text");
        expect(typescript.code).toContain("response.output_text");
      }
    }
  });

  it("offers no invented native dialect", () => {
    expect(nativeSnippets(BASE, model, { api: "video" })).toEqual([]);
  });
});

describe("caller routing recipes", () => {
  it("serializes bounded models and provider fields at the wire top level", async () => {
    const request = submitted(false);
    const models = Array.from({ length: 8 }, (_, index) => `own-provider/fallback-${index}`);
    const provider = { order: ["upstream-a", "upstream-b"], only: ["upstream-a", "upstream-b"], allow_fallbacks: false };
    const [curl, python, typescript] = routingSnippets(BASE, request.model, { models, provider, request });
    const extra = { models: models.slice(0, 5), provider };
    expect(curlBody(curl.code)).toEqual({ ...request, ...extra });
    const py = runPython(python.code);
    expect(py.error).toBeNull();
    expect(py.calls).toEqual([{ ...request, extra_body: extra }]);
    expect(python.code).toContain("extra_body=");
    const ts = await runTypescript(typescript.code);
    expect(ts.error).toBeUndefined();
    expect(ts.create).toHaveBeenCalledTimes(1);
    expect(ts.create).toHaveBeenCalledWith({ ...request, ...extra });
    expect(typescript.code).not.toContain("extra_body");
    expect(python.code).toContain("max_retries=0");
    expect(typescript.code).toContain("maxRetries: 0");
    expect(models).toHaveLength(8);
    expect(request).not.toHaveProperty("models");
  });

  it("keeps false and true provider fallbacks distinct and uses replaceable qualified IDs", () => {
    const [example] = routingSnippets(BASE, MODEL);
    const body = curlBody(example.code);
    expect(body.models).toEqual(["YOUR_PROVIDER_ID/YOUR_FALLBACK_MODEL"]);
    expect(body.provider).toEqual({ order: ["YOUR_UPSTREAM_PROVIDER_SLUG"], only: ["YOUR_UPSTREAM_PROVIDER_SLUG"], allow_fallbacks: false });
    const [custom] = routingSnippets(BASE, MODEL, {
      models: [" own/one ", "own/one", "", "own/two"],
      provider: { order: ["vendor"], only: ["vendor"], allow_fallbacks: true },
    });
    expect(curlBody(custom.code).models).toEqual(["own/one", "own/two"]);
    expect(curlBody(custom.code).provider.allow_fallbacks).toBe(true);
  });
});

describe("audio copy recipes", () => {
  it("exports speech text and voice to a binary output file without chat fields", () => {
    const model = "own-provider/team's-speech";
    const [speech] = modelSnippets("https://gateway.example.test/team", model, { type: "audio_speech", stream: true, request: submitted(true) });
    const args = curlArgs(speech.code);
    expect(args).toContain("https://gateway.example.test/team/v1/audio/speech");
    expect(args[args.indexOf("--output") + 1]).toBe("speech.mp3");
    expect(curlBody(speech.code)).toEqual({ model, input: "Hello", voice: "YOUR_VOICE", response_format: "mp3" });
    expect(args).not.toContain("-N");
    expect(speech.code).not.toContain("messages");
  });

  it("uploads transcription as multipart and requests text on stdout", () => {
    const model = "own-provider/team's-transcriber";
    const [transcription] = modelSnippets(BASE, model, { type: "audio_transcriptions", stream: true });
    const args = curlArgs(transcription.code);
    expect(args).toContain(`${BASE}/audio/transcriptions`);
    const fields = args.filter((_, index) => args[index - 1] === "-F");
    expect(fields).toEqual([`model=${model}`, "file=@./YOUR_AUDIO_FILE.wav", "response_format=text"]);
    expect(transcription.code).not.toMatch(/Content-Type|application\/json|messages|stream/);
    expect(args).not.toContain("-d");
    expect(args).not.toContain("-N");
  });
});
