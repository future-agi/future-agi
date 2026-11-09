// A starter Omega agent: one tool, one model, one run. With no key a scripted model plays the LLM,
// so this runs offline and prints the same lines every time. `omega hub run <id> --live <model>`
// sets OMEGA_LIVE_MODEL and the same agent runs on that model, capped at OMEGA_HUB_MAX_CALLS calls.
import { runAgent, tool } from "@omega/core";
import { lastToolOutputs, liveOrScripted } from "@omega/core/testing";

const OPENING_HOURS: Record<string, string> = { monday: "8:00-18:00", saturday: "9:00-14:00", sunday: "closed" };

export const openingHours = tool({
  name: "opening_hours",
  description: "The opening hours for one day of the week.",
  inputSchema: { day: "string" },
  execute: ({ day }: { day: string }) => ({ day, hours: OPENING_HOURS[day.toLowerCase()] ?? "8:00-18:00" })
});

export function createModel() {
  return liveOrScripted({
    maxCalls: Number(process.env.OMEGA_HUB_MAX_CALLS ?? 2),
    scripted: {
      id: "scripted-starter",
      turns: [
        { content: "", toolCalls: [{ id: "call_1", name: "opening_hours", arguments: { day: "saturday" } }] },
        (request) => {
          const [result] = lastToolOutputs<{ day: string; hours: string }>(request);
          return { content: `On ${result?.output.day} we are open ${result?.output.hours}.` };
        }
      ]
    }
  });
}

export async function run(question = "When are you open on Saturday?") {
  const model = createModel();
  return runAgent(
    { name: "Front desk", instructions: "Answer opening-hours questions. Call opening_hours first, then answer in one sentence.", model: model.id, tools: [openingHours] },
    question,
    { providers: [model] }
  );
}

if (process.argv[1] === import.meta.filename) {
  const result = await run();
  console.log("question: When are you open on Saturday?");
  for (const call of result.toolResults) console.log(`tool ${call.name}: ${JSON.stringify(call.content)}`);
  console.log(`answer: ${String(result.content)}`);
}
