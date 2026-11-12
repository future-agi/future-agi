import { describe, expect, it, vi } from "vitest";

import {
  autoSnippets,
  modelSnippets,
  nativeSnippets,
  routingSnippets,
} from "../models/snippets";
import { discoverySnippets, quickstartSnippets } from "../overview/quickstart";

// A default Node project (npm init -y) is CommonJS, and a copied file.ts runs
// there as CommonJS (npx tsx file.ts): the file is the body of a plain
// function, so an await outside an async function is a syntax error. Each
// TypeScript example is compiled that way here, run against in-memory SDK
// stand-ins, and checked for the request it makes. No SDK is installed and no
// request leaves the test.

// R-LIT synthetic: an RFC 2606 .test gateway URL, made-up model IDs and a key
// value that only the stand-ins below ever read.
const URL = "https://gateway.example.test/v1";
const ROOT = "https://gateway.example.test";
const MODEL = "example-provider/example-chat-model";
const EMBEDDING_MODEL = "example-provider/example-embedding-model";
const KEY = "synthetic-test-key";

const HELLO = [{ role: "user", content: "Hello" }];
const typescriptOf = (tabs) => tabs.find((tab) => tab.key === "typescript").code;

const EXAMPLES = [
  {
    name: "Overview first request",
    code: () => typescriptOf(quickstartSnippets(URL, MODEL)),
    clients: [{ baseURL: URL, apiKey: KEY }],
    calls: [["chat.completions", { model: MODEL, messages: HELLO }]],
    logged: [["answer"]],
  },
  {
    name: "Overview discover models",
    code: () => typescriptOf(discoverySnippets(URL)),
    clients: [{ baseURL: URL, apiKey: KEY }],
    calls: [["models.list"]],
    logged: [[MODEL], ["auto"]],
  },
  {
    name: "Model API chat",
    code: () => typescriptOf(modelSnippets(URL, MODEL, { type: "chat" })),
    clients: [{ baseURL: URL, apiKey: KEY }],
    calls: [["chat.completions", { model: MODEL, messages: HELLO }]],
    logged: [["answer"]],
  },
  {
    name: "Auto routing",
    code: () => typescriptOf(autoSnippets(URL)),
    clients: [{ baseURL: URL, apiKey: KEY }],
    calls: [[
      "chat.completions",
      { model: "auto", messages: HELLO },
      { headers: { "x-agentcc-auto-preference": "cost" } },
    ]],
    logged: [["example-provider/routed-model", "answer"]],
  },
  {
    name: "Model API streamed chat",
    code: () => typescriptOf(modelSnippets(URL, MODEL, { type: "chat", stream: true })),
    clients: [{ baseURL: URL, apiKey: KEY, maxRetries: 0 }],
    calls: [[
      "chat.completions",
      { model: MODEL, messages: HELLO, stream: true, stream_options: { include_usage: true } },
    ]],
    logged: [],
    written: [["partial"], [""]],
    errors: [["Usage:", { total_tokens: 4 }]],
  },
  {
    name: "Model API caller routing",
    code: () => typescriptOf(routingSnippets(URL, MODEL)),
    clients: [{ baseURL: URL, apiKey: KEY, maxRetries: 0 }],
    calls: [[
      "chat.completions",
      {
        model: MODEL,
        messages: HELLO,
        models: ["YOUR_PROVIDER_ID/YOUR_FALLBACK_MODEL"],
        provider: {
          order: ["YOUR_UPSTREAM_PROVIDER_SLUG"],
          only: ["YOUR_UPSTREAM_PROVIDER_SLUG"],
          allow_fallbacks: false,
        },
      },
    ]],
    logged: [["answer"]],
  },
  {
    name: "Model API Messages",
    code: () => typescriptOf(nativeSnippets(URL, MODEL, { api: "messages" })),
    clients: [{ baseURL: ROOT, apiKey: KEY, maxRetries: 0 }],
    calls: [["messages", { model: MODEL, max_tokens: 256, messages: HELLO }]],
    logged: [["answer"]],
  },
  {
    name: "Model API Responses",
    code: () => typescriptOf(nativeSnippets(URL, MODEL, { api: "responses" })),
    clients: [{ baseURL: URL, apiKey: KEY, maxRetries: 0 }],
    calls: [["responses", { model: MODEL, input: "Hello" }]],
    logged: [["answer"]],
  },
  {
    name: "Model API embeddings",
    code: () => typescriptOf(modelSnippets(URL, EMBEDDING_MODEL, { type: "embeddings" })),
    clients: [{ baseURL: URL, apiKey: KEY }],
    calls: [["embeddings", { model: EMBEDDING_MODEL, input: "Hello" }]],
    logged: [[3]],
  },
];

// In-memory stand-ins for the OpenAI and Anthropic clients: they record the
// client options and every call, and answer at once (or fail when asked).
function standIns(failure) {
  const clients = [];
  const calls = [];
  const answer = {
    model: "example-provider/routed-model",
    choices: [{ message: { content: "answer" } }],
    content: [{ type: "text", text: "answer" }],
    output_text: "answer",
    data: [{ embedding: [0.1, 0.2, 0.3] }],
  };
  const create = (endpoint) => async (...args) => {
    calls.push([endpoint, ...args]);
    if (failure) throw failure;
    if (!args[0].stream) return answer;
    return (async function* () {
      yield { choices: [{ delta: { content: "partial" } }] };
      yield { choices: [], usage: { total_tokens: 4 } };
    })();
  };
  class Client {
    constructor(options) {
      clients.push(options);
      this.chat = { completions: { create: create("chat.completions") } };
      this.messages = { create: create("messages") };
      this.responses = { create: create("responses") };
      this.embeddings = { create: create("embeddings") };
      this.models = {
        list: (...args) => {
          calls.push(["models.list", ...args]);
          return (async function* () {
            if (failure) throw failure;
            yield { id: MODEL };
            yield { id: "auto" };
          })();
        },
      };
    }
  }
  return { Client, clients, calls };
}

const IMPORT = /^import (\w+) from "(?:openai|@anthropic-ai\/sdk)";\n/m;
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

// The example's single import becomes a parameter; the rest is the copied
// text, unchanged. Function is a CommonJS module body; AsyncFunction stands
// in for an ES module, where top-level await is allowed.
function compile(code, Body) {
  const [line, name] = code.match(IMPORT) || [];
  expect(line, "one SDK import").toBeTruthy();
  return new Body(name, "console", "process", code.replace(IMPORT, ""));
}

function compileAsCommonJs(code) {
  let execute;
  expect(() => {
    execute = compile(code, Function);
  }).not.toThrow();
  return execute;
}

async function run(execute, failure) {
  const { Client, clients, calls } = standIns(failure);
  const output = { log: vi.fn(), error: vi.fn() };
  const process = { env: { AGENTCC_API_KEY: KEY }, stdout: { write: vi.fn() } };
  await execute(Client, output, process);
  // Let a called main() settle; the stand-ins answer without timers.
  await new Promise((resolve) => setTimeout(resolve, 0));
  return { clients, calls, output, process };
}

function expectRequest(result, example) {
  expect(result.clients).toEqual(example.clients);
  expect(result.calls).toEqual(example.calls);
  expect(result.output.log.mock.calls).toEqual(example.logged);
  expect(result.process.stdout.write.mock.calls).toEqual(example.written || []);
  expect(result.output.error.mock.calls).toEqual(example.errors || []);
  expect(result.process.exitCode).toBeUndefined();
}

describe("TypeScript examples in a default (CommonJS) Node project", () => {
  it("covers every TypeScript example the gateway screens build", () => {
    expect(EXAMPLES).toHaveLength(9);
    for (const example of EXAMPLES) expect(example.code(), example.name).toContain("await ");
  });

  it.each(EXAMPLES)("$name: has no top-level await and keeps its imports at the top", (example) => {
    const code = example.code();
    compileAsCommonJs(code);
    const lines = code.split("\n");
    const firstCode = lines.findIndex((line) => line && !line.startsWith("//") && !line.startsWith("import "));
    expect(lines.slice(firstCode).some((line) => line.startsWith("import "))).toBe(false);
    expect(lines.filter((line) => line === "async function main() {")).toHaveLength(1);
    expect(lines.filter((line) => line.startsWith("main()"))).toEqual(["main().catch((error) => {"]);
  });

  it.each(EXAMPLES)("$name: runs as CommonJS and sends the same request", async (example) => {
    expectRequest(await run(compileAsCommonJs(example.code())), example);
  });

  it.each(EXAMPLES)("$name: sends the same request as an ES module", async (example) => {
    expectRequest(await run(compile(example.code(), AsyncFunction)), example);
  });

  it.each(EXAMPLES)("$name: prints a failed request and exits non-zero", async (example) => {
    const failure = new Error("upstream refused the request");
    const result = await run(compileAsCommonJs(example.code()), failure);
    expect(result.output.error).toHaveBeenCalledWith(failure);
    expect(result.process.exitCode).toBe(1);
    expect(result.output.log).not.toHaveBeenCalled();
  });
});

// What the cURL and Python tabs of the four main examples show, byte for byte.
const UNCHANGED = [
  {
    name: "Overview first request",
    tabs: () => quickstartSnippets(URL, MODEL),
    curl: [
      "curl 'https://gateway.example.test/v1/chat/completions' \\",
      '  -H "Authorization: Bearer $AGENTCC_API_KEY" \\',
      '  -H "Content-Type: application/json" \\',
      "  -d '{",
      '    "model": "example-provider/example-chat-model",',
      '    "messages": [{"role": "user", "content": "Hello"}]',
      "  }'",
    ],
    python: [
      "# Install: python -m pip install openai",
      "import os",
      "from openai import OpenAI",
      "",
      "client = OpenAI(",
      '    base_url="https://gateway.example.test/v1",',
      '    api_key=os.environ["AGENTCC_API_KEY"],',
      ")",
      "",
      "response = client.chat.completions.create(",
      '    model="example-provider/example-chat-model",',
      '    messages=[{"role": "user", "content": "Hello"}],',
      ")",
      "print(response.choices[0].message.content)",
    ],
  },
  {
    name: "Overview discover models",
    tabs: () => discoverySnippets(URL),
    curl: [
      "# First set AGENTCC_API_KEY to your gateway API key:",
      '# export AGENTCC_API_KEY="YOUR_GATEWAY_API_KEY"',
      "curl 'https://gateway.example.test/v1/models' \\",
      '  -H "Authorization: Bearer $AGENTCC_API_KEY"',
    ],
    python: [
      "# Install: python -m pip install openai",
      "# Set AGENTCC_API_KEY to your gateway API key before running.",
      "import os",
      "from openai import OpenAI",
      "",
      "client = OpenAI(",
      '    base_url="https://gateway.example.test/v1",',
      '    api_key=os.environ["AGENTCC_API_KEY"],',
      ")",
      "for model in client.models.list():",
      "    print(model.id)",
    ],
  },
  {
    name: "Model API chat",
    tabs: () => modelSnippets(URL, MODEL, { type: "chat" }),
    curl: [
      "curl https://gateway.example.test/v1/chat/completions \\",
      '  -H "Authorization: Bearer $AGENTCC_API_KEY" \\',
      '  -H "Content-Type: application/json" \\',
      "  -d '{",
      '    "model": "example-provider/example-chat-model",',
      '    "messages": [{"role": "user", "content": "Hello"}]',
      "  }'",
    ],
    python: [
      "import os",
      "from openai import OpenAI",
      "",
      "client = OpenAI(",
      '    base_url="https://gateway.example.test/v1",',
      '    api_key=os.environ["AGENTCC_API_KEY"],',
      ")",
      "",
      "response = client.chat.completions.create(",
      '    model="example-provider/example-chat-model",',
      '    messages=[{"role": "user", "content": "Hello"}],',
      ")",
      "print(response.choices[0].message.content)",
    ],
  },
  {
    name: "Auto routing",
    tabs: () => autoSnippets(URL),
    curl: [
      "curl https://gateway.example.test/v1/chat/completions \\",
      '  -H "Authorization: Bearer $AGENTCC_API_KEY" \\',
      '  -H "Content-Type: application/json" \\',
      '  -H "x-agentcc-auto-preference: cost" \\',
      "  -d '{",
      '    "model": "auto",',
      '    "messages": [{"role": "user", "content": "Hello"}]',
      "  }'",
    ],
    python: [
      "import os",
      "from openai import OpenAI",
      "",
      "client = OpenAI(",
      '    base_url="https://gateway.example.test/v1",',
      '    api_key=os.environ["AGENTCC_API_KEY"],',
      ")",
      "",
      "response = client.chat.completions.create(",
      '    model="auto",',
      '    messages=[{"role": "user", "content": "Hello"}],',
      '    extra_headers={"x-agentcc-auto-preference": "cost"},',
      ")",
      "print(response.model, response.choices[0].message.content)",
    ],
  },
];

describe("the other tabs of the same examples", () => {
  it.each(UNCHANGED)("$name: cURL and Python are unchanged", ({ tabs, curl, python }) => {
    const byKey = Object.fromEntries(tabs().map((tab) => [tab.key, tab.code]));
    expect(Object.keys(byKey)).toEqual(["curl", "python", "typescript"]);
    expect(byKey.curl).toBe(curl.join("\n"));
    expect(byKey.python).toBe(python.join("\n"));
  });
});
