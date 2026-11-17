// Triage a support message: which team should answer it, and how soon.
//
// The workflow is input -> the triage_message tool -> output. The tool reads
// the words the message uses, so the project needs no model key, no network
// and no package beyond @omega/core, which the deployment runtime provides.
// Swap the tool for an agent step when you want a model to decide.
import {
  createOmega,
  defineWorkflow,
  InMemoryTraceSink,
  inputStep,
  outputStep,
  tool,
  toolStep,
} from "@omega/core";

export interface Triage {
  team: "billing" | "access" | "bugs" | "general";
  priority: "high" | "normal";
  /** The words that decided it, in the order they were checked. */
  matched: string[];
}

// The first team whose words the message uses answers it.
const TEAMS: ReadonlyArray<{ team: Triage["team"]; words: string[] }> = [
  {
    team: "billing",
    words: ["invoice", "refund", "charged", "charge", "payment", "card"],
  },
  {
    team: "access",
    words: ["password", "log in", "login", "sign in", "locked out", "2fa"],
  },
  {
    team: "bugs",
    words: ["error", "crash", "broken", "bug", "not working", "fails"],
  },
];

// Any of these makes it high priority.
const URGENT = ["urgent", "asap", "outage", "down", "immediately"];

/** Whole words only: "down" matches "the site is down", never "download". */
function uses(message: string, word: string): boolean {
  const words = ` ${message.toLowerCase().replace(/[^a-z0-9]+/g, " ")} `;
  return words.includes(` ${word} `);
}

export function triage(message: string): Triage {
  const match = TEAMS.find((entry) =>
    entry.words.some((word) => uses(message, word)),
  );
  const urgent = URGENT.filter((word) => uses(message, word));
  return {
    team: match ? match.team : "general",
    priority: urgent.length > 0 ? "high" : "normal",
    matched: [
      ...(match ? match.words.filter((word) => uses(message, word)) : []),
      ...urgent,
    ],
  };
}

export const triageMessage = tool({
  name: "triage_message",
  description:
    "Name the team that should answer a support message, and its priority, from the words the message uses.",
  inputSchema: {
    type: "object",
    properties: { message: { type: "string" } },
  },
  // The run's input reaches the tool as it was sent: {"message": "..."}, or
  // {"input": "..."} when the run's input is a plain string.
  execute: async (args: { message?: unknown; input?: unknown }) => {
    const message =
      typeof args.message === "string"
        ? args.message
        : typeof args.input === "string"
          ? args.input
          : "";
    return { ...triage(message) };
  },
});

export const workflow = defineWorkflow({
  id: "triage",
  name: "Triage a support message",
  steps: [
    inputStep("input"),
    toolStep("triage", triageMessage),
    outputStep("final"),
  ],
});

export const app = createOmega({ trace: new InMemoryTraceSink() });
