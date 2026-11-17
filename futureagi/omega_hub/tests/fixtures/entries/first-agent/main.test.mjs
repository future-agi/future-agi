// The keyless test omega hub check runs.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { createModel, getWeather, runFirstAgent } from "./main.mjs";

// Keyless by construction, even if the shell exported OMEGA_LIVE_MODEL=1.
delete process.env.OMEGA_LIVE_MODEL;

test("the agent calls get_weather with the city, then answers from the tool result", async () => {
  const model = createModel();
  const result = await runFirstAgent({ model });

  // The model was called twice: once to pick the tool, once to answer.
  assert.equal(model.calls, 2);
  assert.deepEqual(model.requests[0].tools.map((schema) => schema.name), ["get_weather"]);

  // The model's tool call reached the tool with its arguments, and the tool ran.
  const assistantTurn = result.messages.find((message) => message.role === "assistant" && message.toolCalls?.length);
  assert.deepEqual(assistantTurn.toolCalls.map((call) => [call.name, call.arguments]), [["get_weather", { city: "Lisbon" }]]);
  assert.equal(result.toolResults.length, 1);
  assert.equal(result.toolResults[0].name, "get_weather");
  assert.equal(result.toolResults[0].ok, true);
  assert.deepEqual(result.toolResults[0].content, { city: "Lisbon", forecast: "sunny, 24 degrees" });

  // The second model call was shown that result, and the final answer is built from it.
  const shown = model.requests[1].messages.filter((message) => message.role === "tool");
  assert.deepEqual(shown.map((message) => JSON.parse(message.content)), [{ city: "Lisbon", forecast: "sunny, 24 degrees" }]);
  assert.equal(result.content, "In Lisbon today: sunny, 24 degrees.");
});

test("the tool's shorthand schema is a strict JSON Schema object", () => {
  assert.deepEqual(getWeather.inputSchema, {
    type: "object",
    properties: { city: { type: "string" } },
    required: ["city"],
    additionalProperties: false
  });
});

test("node main.mjs prints the answer", () => {
  const env = { ...process.env };
  delete env.OMEGA_LIVE_MODEL;
  const run = spawnSync(process.execPath, [fileURLToPath(new URL("./main.mjs", import.meta.url))], { env, encoding: "utf8" });
  assert.equal(run.status, 0, run.stderr);
  assert.match(run.stdout, /^answer: In Lisbon today: sunny, 24 degrees\.$/m);
  assert.match(run.stdout, /^tool get_weather ok=true -> \{"city":"Lisbon","forecast":"sunny, 24 degrees"\}$/m);
});
