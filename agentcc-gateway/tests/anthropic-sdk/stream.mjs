import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";

// Resolve the exact, cloud-installed SDK without adding a product dependency.
const require = createRequire(process.env.SDK_NODE_PACKAGE);
const Anthropic = require("@anthropic-ai/sdk").default;
const sdkManifest = join(
  dirname(process.env.SDK_NODE_PACKAGE),
  "node_modules/@anthropic-ai/sdk/package.json",
);
assert.equal(JSON.parse(readFileSync(sdkManifest, "utf8")).version, "1.3.0");
const [baseURL, model, scenario] = process.argv.slice(2);
assert.equal(new URL(baseURL).hostname, "127.0.0.1");
const client = new Anthropic({
  baseURL,
  apiKey: process.env.SDK_FIXTURE_KEY,
  maxRetries: 0,
  timeout: 15_000,
});
const params = {
  model,
  max_tokens: 64,
  messages: [{ role: "user", content: "hi" }],
};
if (scenario === "tool") {
  params.tools = [
    {
      name: "weather",
      input_schema: {
        type: "object",
        properties: { city: { type: "string" } },
        required: ["city"],
      },
    },
  ];
}
let text = "";
const stream = client.messages.stream(params).on("text", (delta) => {
  text += delta;
});
try {
  const final = await stream.finalMessage();
  assert.notEqual(scenario, "error", "upstream failure was silently accepted");
  assert.equal(final.stop_sequence, null);
  assert.ok(final.usage.input_tokens > 0);
  assert.ok(final.usage.output_tokens > 0);
  if (scenario !== "estimated") {
    assert.equal(final.usage.input_tokens, 9);
    assert.equal(final.usage.output_tokens, 4);
  }
  if (scenario === "tool") {
    assert.equal(text, "");
    assert.equal(final.stop_reason, "tool_use");
    assert.equal(final.content.length, 1);
    const block = final.content[0];
    assert.equal(block.type, "tool_use");
    assert.equal(block.id, "toolu_test");
    assert.equal(block.name, "weather");
    assert.deepEqual(block.input, { city: "Paris" });
  } else {
    assert.equal(text, "Hello world");
    assert.equal(final.stop_reason, "end_turn");
    assert.equal(final.content.at(-1).type, "text");
    assert.equal(final.content.at(-1).text, text);
    if (scenario === "thinking" && process.env.SDK_FIXTURE_NATIVE === "true") {
      assert.equal(final.content.length, 2);
      assert.equal(final.content[0].type, "thinking");
      assert.equal(final.content[0].thinking, "Consider");
      assert.equal(final.content[0].signature, "synthetic-signature");
    } else {
      // Canonical reasoning_content is unsupported, not a thinking block.
      assert.equal(final.content.length, 1);
      assert.equal(final.content[0].type, "text");
      assert.equal(final.content[0].text, text);
    }
  }
  console.log(
    JSON.stringify({
      sdk: "typescript",
      scenario,
      text,
      usage: final.usage,
      stop_reason: final.stop_reason,
    }),
  );
} catch (error) {
  // A typed upstream error is expected; accumulator exceptions are not.
  assert.equal(scenario, "error");
  assert.ok(error instanceof Anthropic.APIError, error);
  assert.match(error.message, /synthetic stream failure/);
  assert.equal(text, "Hello world");
  console.log(
    JSON.stringify({
      sdk: "typescript",
      scenario,
      expected_api_error: error.constructor.name,
      partial_text: text,
    }),
  );
}
