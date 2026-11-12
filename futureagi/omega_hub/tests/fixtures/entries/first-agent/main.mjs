/**
 * your first agent. One tool, one model, one runAgent call.
 *
 * Keyless by default: a scripted model plays the LLM. It asks for the tool, then answers from the
 * tool's result, so this runs with no API key and no network. With OMEGA_LIVE_MODEL set (and
 * OMEGA_VERTEX_CREDS) the same agent runs on Gemini through Vertex AI, capped at two model calls.
 *
 *     node main.mjs
 */
import { runAgent, tool } from "@omega/core";
import { isMain } from "./lib.mjs";
import { lastToolOutputs, liveOrScripted } from "@omega/core/testing";

// 1. A tool: a name and a description the model reads, an input schema, and the code that runs.
//    `{ city: "string" }` is shorthand for a JSON Schema object with one required string property.
const FORECASTS = { lisbon: "sunny, 24 degrees", oslo: "light rain, 9 degrees" };

export const getWeather = tool({
  name: "get_weather",
  description: "Today's forecast for a city.",
  inputSchema: { city: "string" },
  execute: ({ city }) => ({ city, forecast: FORECASTS[city.toLowerCase()] ?? "no forecast available" })
});

// 2. A model. The scripted one asks for get_weather on its first call and answers on its second,
//    reading the tool result the runtime sent back. A live model decides both steps itself.
export function createModel() {
  return liveOrScripted({
    scripted: {
      id: "scripted-weather",
      turns: [
        { content: "", toolCalls: [{ id: "call_1", name: "get_weather", arguments: { city: "Lisbon" } }] },
        (request) => {
          const [weather] = lastToolOutputs(request);
          return { content: `In ${weather.output.city} today: ${weather.output.forecast}.` };
        }
      ]
    }
  });
}

// 3. The agent, run once. Its `model` must equal the provider's `id`: that is how the runtime
//    finds the provider. runAgent returns the answer plus every tool result and the token usage.
export async function runFirstAgent({ model = createModel(), question = "What's the weather in Lisbon today?" } = {}) {
  return runAgent(
    {
      name: "Weather helper",
      instructions: "Answer weather questions. Call get_weather before you answer, then reply in one sentence.",
      model: model.id,
      tools: [getWeather]
    },
    question,
    { providers: [model] }
  );
}

if (isMain(import.meta.url)) {
  const result = await runFirstAgent();
  console.log("answer:", result.content);
  for (const call of result.toolResults) {
    console.log(`tool ${call.name} ok=${call.ok} ->`, JSON.stringify(call.content));
  }
  // The scripted model reports no token usage; a live model does.
  if (result.usage) console.log("usage:", JSON.stringify(result.usage));
}
