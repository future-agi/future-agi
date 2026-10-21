import { PROVIDER_LABELS } from "./agentRegistry";

/*
  An agent read from its own production traces.

  Spans carry what an agent ran with: the model, the system prompt and the
  tool definitions it sent. That's enough to save it as an agent and test
  its prompt and tools on our model ("Prompt only") straight away — and the
  Observe project they came from is linked from the start, so Error Feed and
  production views work for it at once.

  What traces can't say is where the agent runs. When they come from a
  platform's call logs or from traceAI in code, they do say which platform
  or framework — and connecting it there is better (live or sealed runs,
  fixes in the account or as pull requests), so that's offered first.
*/

const count = (traces) => Number(String(traces || "").match(/[\d,]+/)?.[0]?.replace(/,/g, "") || 0);

/* What a project's spans most plausibly hold, by what it's for. */
const TOOLSETS = [
  { match: /return|refund/i, tools: [["lookup_order", "Find an order by its number or the caller's email"], ["get_return_window", "Whether an order can still be returned"], ["issue_refund", "Refund an order, or part of it"]] },
  { match: /support|storefront/i, tools: [["lookup_order", "Find an order by its number or the caller's email"], ["shipment_status", "Where a shipment is now"], ["escalate_to_human", "Hand the conversation to a person"]] },
  { match: /airline|rebook|travel/i, tools: [["search_flights", "Flights between two airports on a date"], ["rebook_passenger", "Move a booking to another flight"], ["issue_voucher", "A travel credit for a disrupted trip"]] },
  { match: /booking|guest|hotel/i, tools: [["check_availability", "Open rooms or slots for given dates"], ["create_booking", "Hold a reservation"], ["cancel_booking", "Cancel a reservation"]] },
  { match: /clinic|appointment/i, tools: [["find_slot", "The next open appointment slots"], ["book_appointment", "Book a patient into a slot"], ["send_reminder", "Text a reminder for an appointment"]] },
  { match: /billing|invoice/i, tools: [["get_invoice", "An invoice by number"], ["issue_credit", "Credit a customer's account"], ["update_payment_method", "Change the card on file"]] },
];
const GENERIC = [["lookup_account", "Find the caller's account"], ["create_ticket", "Open a support ticket"], ["handoff_to_human", "Hand the conversation to a person"]];

const toolsFor = (name, named) => {
  if (named?.length) return named.map((n) => ({ name: n, desc: "", args: [] }));
  const set = TOOLSETS.find((t) => t.match.test(name))?.tools || GENERIC;
  return set.map(([n, desc]) => ({ name: n, desc, args: [] }));
};

const promptFor = (name, tools) => [
  `You are the ${name} assistant.`,
  "Help the customer with their request using the tools you have, and confirm what you did before you finish.",
  `Tools: ${tools.map((t) => t.name).join(", ")}.`,
  "If you can't help, say so plainly and hand over to a person.",
].join("\n");

/**
 * What an Observe project's traces say about the agent behind them.
 * → { route: "platform" | "code" | "traces", traceCount, model, prompt,
 *     tools, channel, framework?, language?, provider?, providerLabel?, traced? }
 */
export const agentReadFromTraces = (project) => {
  if (!project) return null;
  const traced = project.traced || null;
  const traceCount = count(project.traces);
  const tools = toolsFor(project.name, traced?.tools);
  const base = {
    traceCount,
    model: traced?.model || "gpt-4o",
    prompt: promptFor(project.name, tools),
    tools,
    channel: traced?.channel || traced?.agentType || (/line|call|phone/i.test(project.name) ? "voice" : "chat"),
    traced,
  };
  if (traced?.via === "call-logs") {
    return { ...base, route: "platform", provider: traced.provider, providerLabel: PROVIDER_LABELS[traced.provider] || traced.provider };
  }
  if (traced?.via === "traceai") {
    return { ...base, route: "code", framework: traced.framework, language: traced.language };
  }
  return { ...base, route: "traces" };
};

/* The first trace from a project just set up — what its spans report. */
export const firstTraceRead = ({ project, framework, channel = "chat" }) => {
  const tools = toolsFor(project, null);
  return {
    route: "traces",
    traceCount: 1,
    spans: 7,
    model: framework === "OpenAI Agents SDK" ? "gpt-4.1" : "gpt-4o",
    prompt: promptFor(project, tools),
    tools,
    channel,
    framework,
  };
};
