/*
  Platform agents (roadmap platforms.md, Week 2): what we read before
  connecting a Vapi/Retell/… agent — its versions where the platform has
  them, a read-only preview of prompt, tools and channel, and for each tool
  whether a test redirects it to the test world or it still runs for real.
  A platform-native action (a transfer, a text message) can't be redirected;
  it's named before any call, never hidden behind "sandboxed".
*/
import { hashSeed } from "./runStream";

/* Actions the platform itself performs — they don't go through your server, so we can't redirect them. */
const NATIVE = {
  vapi: [
    { name: "transferCall", live: "Rings the real destination number", risky: true },
    { name: "endCall", live: "Ends the call — nothing outside it changes", risky: false },
  ],
  retell: [
    { name: "transfer_call", live: "Rings the real destination number", risky: true },
    { name: "end_call", live: "Ends the call — nothing outside it changes", risky: false },
    { name: "press_digit", live: "Sends a keypad tone on the call", risky: false },
  ],
  bland: [{ name: "transfer", live: "Rings the real destination number", risky: true }],
  elevenlabs: [
    { name: "transfer_to_number", live: "Rings the real destination number", risky: true },
    { name: "end_call", live: "Ends the call — nothing outside it changes", risky: false },
  ],
};

/* Custom tools, guessed from what the assistant is for — they call your server, so a test points them at the world. */
const CUSTOM = [
  [/clinic|appoint|schedul|booking|book|resched/i, ["check_availability", "book_appointment", "cancel_appointment"]],
  [/insur|claim|coverage/i, ["lookup_policy", "check_coverage", "open_claim"]],
  [/order|delivery|return/i, ["lookup_order", "issue_refund", "update_delivery"]],
  [/lead|sales|qualif/i, ["lookup_contact", "create_lead"]],
  [/concierge|reserv|table/i, ["check_tables", "create_reservation"]],
];

const customToolsOf = (assistant) =>
  (CUSTOM.find(([re]) => re.test(assistant?.name || "")) || [null, ["lookup_customer", "create_ticket"]])[1];

/** Platforms that version an agent, and what a version is called there. */
export const versioningOf = (provider) =>
  ({
    retell: { kind: "versions", note: "Retell publishes numbered versions — the run is pinned to the one you pick." },
    elevenlabs: { kind: "branches", note: "ElevenLabs branches pin once versioning is on for the agent." },
    vapi: { kind: "none", note: "Vapi doesn't version assistants. We snapshot its config when you connect; runs are marked unpinned if it changes." },
  })[provider] || { kind: "none", note: "This platform doesn't version agents — runs are marked unpinned if it changes." };

/** The versions to pick from, newest first; [] when the platform has none. */
export const versionsOf = (provider, assistant) => {
  if (!assistant || versioningOf(provider).kind === "none") return [];
  const n = 8 + (hashSeed(assistant.id) % 9);
  return [
    { id: `v${n}`, label: `v${n}`, note: "published · in use", published: true },
    { id: `v${n - 1}`, label: `v${n - 1}`, note: "published", published: true },
    { id: "draft", label: "Draft", note: "unpublished — changes as it's edited", published: false },
  ];
};

/**
 * The read-only preview: { prompt, tools: [{ name, kind: "custom" | "native",
 * handling: "redirected" | "live", line, risky }], channel }.
 * `sealed` — a test copy we control; otherwise the live agent in your account.
 */
export const platformPreviewOf = (provider, assistant, { sealed = true, voice = true } = {}) => {
  if (!assistant) return null;
  const custom = customToolsOf(assistant).map((name) => ({
    name,
    kind: "custom",
    handling: "redirected",
    line: sealed
      ? "Calls your server — a test points it at the test world"
      : "Calls your server — a test points it at the test world through a server-URL override",
    risky: false,
  }));
  const native = (NATIVE[provider] || []).map((t) => ({ ...t, kind: "native", handling: "live", line: t.live }));
  return {
    prompt: `You are ${assistant.name}, a ${voice ? "phone" : "chat"} assistant. Greet the caller, confirm who they are before changing anything, and use your tools — never make up availability or account details. If you can't help, ${native.some((t) => t.risky) ? "transfer to a person" : "say so and offer what you can"}.`,
    tools: [...custom, ...native],
    channel: voice ? (assistant.phone ? `Phone ${assistant.phone} · web calls` : "Web calls only") : "Chat",
  };
};

/** The CLI line that registers the same agent. */
export const platformAddCli = ({ provider, agentId, name, keyRef, voice }) =>
  `agent-learn agent add "${name || agentId}" --type platform --provider ${provider} --provider-agent ${agentId} --channel ${voice ? "audio" : "text"}${keyRef ? ` --secret-ref api_key=${keyRef}` : ""}`;
