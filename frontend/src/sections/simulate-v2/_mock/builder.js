/**
 * The builder, as the UI sees it.
 *
 * Four stages, mirroring the real package: understand → build → scenarios →
 * run. The important property is that the contract is *read*, never asked for:
 * the tools come back with exact argument names and permitted values, which a
 * person typing into a form could not supply and nothing could verify.
 *
 * Narrated deterministically rather than executed, so a demo replays the same
 * way every time.
 */

import { getAgentType, agentTypesForSurface } from "./agentTypes";

/**
 * The providers come off the voice_platform agent type rather than a second
 * list here. One place defines who we integrate with; adding a provider there
 * makes it connectable and derivable in the same commit.
 */
export const PLATFORM_PROVIDERS =
  getAgentType("voice_platform")?.fields.find((f) => f.key === "provider")?.options || [];

/** Providers for a modality, read off that modality's agent type. */
export const providersFor = (modality) => {
  const typeId = PLATFORMS_BY_MODALITY[modality];
  if (!typeId) return [];
  return getAgentType(typeId)?.fields.find((f) => f.key === "provider")?.options || [];
};

/** The sources on offer for a modality, in the order they are listed. */
export const sourceKindsFor = (modality) => {
  const allowed = MODALITY_SOURCES[modality] || MODALITY_SOURCES.custom;
  return SOURCE_KINDS.filter((s) => allowed.includes(s.id));
};

/**
 * Which sources make sense for which kind of agent.
 *
 * Asking the modality first is not a retreat from "we read rather than ask".
 * It is not knowable before there is a source, and it decides which questions
 * are even legitimate: the hosted-platform providers are all voice, so
 * offering them to someone with a coding agent is simply wrong. Detection
 * still runs afterwards and can correct the answer.
 */
export const MODALITY_SOURCES = {
  voice: ["repo", "endpoint", "platform", "mcp", "upload"],
  chat: ["repo", "endpoint", "mcp", "upload"],
  cua: ["repo", "endpoint", "upload"],
  coding: ["repo", "mcp", "upload"],
  physical: ["repo", "upload"],
  custom: ["repo", "endpoint", "platform", "mcp", "upload"],
};

/**
 * Hosted platforms we actually integrate with, by modality. Voice is the only
 * one with a provider roster today — the others reach their agents by repo or
 * endpoint, and inventing a list here would promise integrations we do not
 * have.
 */
export const PLATFORMS_BY_MODALITY = { voice: "voice_platform" };

/**
 * How a run can reach this agent, given what kind it is.
 *
 * A list, not a single answer. Defaulting a repo-hosted voice agent to raw SIP
 * demanded a trunk URI, username, password and codec from someone who has
 * almost certainly deployed on a platform instead — the rarest option asked
 * the most invasive questions. So the common case leads and the specialist one
 * is there for whoever needs it.
 */
const MODALITY_SURFACE = {
  voice: "voice", chat: "chat", cua: "browser",
  coding: "cli", physical: "sim", custom: "chat",
};

export const runtimeTypesFor = (modality) => {
  const surface = MODALITY_SURFACE[modality];
  if (!surface) return [];
  return agentTypesForSurface(surface).recommended;
};

/**
 * A hosted-platform source settles the question — we already know how to reach
 * it. Anything else offers the choice.
 */
export const runtimeTypeFor = (modality, sourceKind, chosenId) => {
  if (sourceKind === "platform") return getAgentType(PLATFORMS_BY_MODALITY[modality] || "voice_platform");
  const list = runtimeTypesFor(modality);
  return list.find((t) => t.id === chosenId) || list[0] || null;
};

export const sourceOwnedKeys = (kind) => {
  if (kind === "platform") return ["provider", "apiKey", "agentId"];
  if (kind === "endpoint") return ["endpoint"];
  return [];
};

export const runtimeValuesFrom = (source) => {
  if (!source) return {};
  if (source.kind === "platform") {
    return {
      provider: source.provider,
      apiKey: source.credential || undefined,
      agentId: source.value,
    };
  }
  if (source.kind === "endpoint") return { endpoint: source.value };
  return {};
};

export const runtimeGap = (source) => {
  if (!source) return null;
  if (carriesRuntimeAccess(source)) {
    return {
      reusable: true,
      title: "Carried over from the source you connected",
      note: source.kind === "platform"
        ? "You pointed us at a hosted agent, so we already hold the provider and assistant. Confirm the credential and this step is done."
        : "You pointed us at a running endpoint, so we already have where to reach it. Confirm how it authenticates and this step is done.",
    };
  }
  return {
    reusable: false,
    title: "Reading your agent is not the same as running it",
    note: source.kind === "mcp"
      ? "The manifest told us which tools exist. It does not tell us how to hold a conversation with your agent, so a run still needs somewhere to call."
      : "We read the source to derive the contract — that told us what your agent can do, not how to reach the deployed one. A run has to call it turn by turn, which needs an endpoint and credentials.",
  };
};

export const SOURCE_KINDS = [
  {
    id: "repo",
    label: "Source repository",
    blurb: "We read the code: tools, prompts, guardrails and the data it touches.",
    icon: "solar:code-square-linear",
    placeholder: "https://github.com/your-org/your-agent",
    depth: "Deepest — exact argument names and permitted values",
  },
  {
    id: "endpoint",
    label: "Running agent",
    blurb: "We talk to a deployed agent and infer its shape from how it answers.",
    icon: "solar:plug-circle-linear",
    placeholder: "https://api.yourapp.com/agent",
    depth: "Inferred from behaviour, confirmed with you",
  },
  {
    id: "platform",
    label: "Hosted agent platform",
    blurb: "Vapi, Retell, Bland, ElevenLabs, LiveKit — the agent lives on their platform, not in a repo you can point at.",
    icon: "solar:phone-calling-rounded-linear",
    placeholder: "asst_9f2c…",
    depth: "Exact — we read the assistant config from the provider",
    platform: true,
  },
  {
    id: "mcp",
    label: "MCP server",
    blurb: "The tool surface your agent calls, not the agent itself — we read the manifest for exact tool schemas, and nothing else is in there.",
    icon: "solar:widget-4-linear",
    placeholder: "https://mcp.yourapp.com/v1/mcp",
    depth: "Tools only — an MCP manifest carries no prompt, rules or data",
  },
  {
    id: "upload",
    label: "Code upload / SDK bundle",
    blurb: "No repo access needed — upload a zip of the source or an SDK bundle.",
    icon: "solar:upload-square-linear",
    placeholder: "support-agent-v4.zip",
    depth: "Same as a repo — it is the same code",
    upload: true,
  },

];

/**
 * A branch name is not a version. We resolve whatever you give us to an exact
 * commit and record that, so a result months later still says what it ran.
 */
/**
 * A branch name is not a version. We resolve whatever you give us to an exact
 * commit and record that, so a result months later still says what it ran.
 */
export const REF_KINDS = [
  { id: "branch", label: "Branch", placeholder: "main", note: "Resolved to the head commit at read time." },
  { id: "tag", label: "Tag", placeholder: "v2.4.0", note: "Pinned. Will not move." },
  { id: "commit", label: "Commit", placeholder: "a41c9e2", note: "Pinned. Exactly this." },
];

/**
 * What the Context Derivation Engine produces, keyed by the stage that
 * produces it. Named for the outputs rather than the machinery, because the
 * outputs are what a user is waiting for.
 */
export const DERIVATION_OUTPUTS = [
  {
    id: "understand",
    label: "Capability graph",
    icon: "solar:siderbar-linear",
    blurb: "Tools, flows, personas and guardrails, read from the source rather than typed in.",
  },
  {
    id: "build",
    label: "Sandbox environment",
    icon: "solar:shield-keyhole-linear",
    blurb: "A shadow agent and a seeded world. Your production system is never involved.",
  },
  {
    id: "scenarios",
    label: "Draft actors + scenario suite",
    icon: "solar:layers-minimalistic-linear",
    blurb: "A first suite, v1 — every scenario proved solvable, non-vacuous and pointed at a rule.",
  },
];

/**
 * What kind of agent this is, *detected* rather than asked.
 *
 * The instinct to ask up front is reasonable and wrong for the same reason we
 * do not ask for tools: a repo that imports livekit is a voice agent whether
 * or not anyone ticked a box, and reading is both more accurate and less work.
 * So we detect, show what we found, and let it be corrected — the correction
 * being the adapter on the RL contract, which already exists.
 */
const STACK_BY_PROVIDER = {
  vapi: "Vapi · Deepgram STT · ElevenLabs TTS",
  retell: "Retell AI · built-in transport",
  bland: "Bland.ai · pathways",
  elevenlabs: "ElevenLabs Agents",
  livekit: "LiveKit Agents · Deepgram · Cartesia",
};

export const detectedStack = (source) => {
  if (source?.kind === "platform") {
    const p = source.provider || "vapi";
    return {
      modality: "voice",
      stack: STACK_BY_PROVIDER[p] || "Custom voice platform",
      how: "Read from the assistant config on your provider.",
    };
  }
  if (source?.kind === "mcp") {
    return { modality: "coding", stack: "MCP server", how: "Read from the tool manifest." };
  }
  if (source?.kind === "endpoint") {
    return { modality: "chat", stack: "HTTP endpoint · SSE", how: "Inferred from how it answers, then confirmed with you." };
  }
  return {
    modality: "voice",
    stack: "LiveKit Agents · Deepgram · Cartesia",
    how: "Read from the imports and call sites in the source.",
  };
};

/*
  `chip` is the button that STARTS this stage, not the one the stage emits.
  The two were swapped, so every chip re-ran the stage before it and the last
  one shadowed the adopt branch entirely — there was no way off the screen.
  `understand` has no chip because reading the source is what starts it.
*/
/**
 * Reading an agent and driving one are different powers.
 *
 * Stage 1 reads: source, assistant config, tool manifest. That tells us what
 * the agent CAN do. Running a simulation means calling it turn by turn, which
 * needs an endpoint and credentials — and a repo gives us neither.
 */
export const carriesRuntimeAccess = (source) =>
  !!source && (source.kind === "endpoint" || source.kind === "platform");

/** The label a provider uses for the thing you paste in. */
export const assistantIdLabel = (provider) =>
  (getAgentType("voice_platform")?.fields.find((f) => f.key === "agentId")
    ?.labelFrom?.map?.[provider]) || { label: "Agent ID", placeholder: "asst_9f2c…" };

export const HARNESS_STAGES = [
  { id: "understand", label: "Understand", chip: null },
  { id: "build", label: "Build", chip: "build the world" },
  { id: "scenarios", label: "Scenarios", chip: "write the scenarios" },
];

/** The chip that leaves this screen — checked before any stage lookup. */
export const ADOPT_CHIP = "use this environment";

const think = (text) => ({ kind: "think", text });
const tool = (label, result) => ({ kind: "tool", label, result });
const file = (path, note) => ({ kind: "file", path, note });
const note = (text) => ({ kind: "note", text });
const json = (label, value) => ({ kind: "json", label, value });

const TOOLS = [
  { name: "verify_identity", args: ["phone", "postcode"], desc: "Match a caller to an account before touching it." },
  { name: "send_otp", args: [], desc: "Text a one-time code to the number on file." },
  { name: "check_otp", args: ["code"], desc: "Verify a code the caller reads back. Never inferred." },
  { name: "create_guest_customer", args: ["first_name"], desc: "Open a guest record when a caller has no account." },
  { name: "lookup_order", args: ["order_id"], desc: "Fetch an order, its status and delivery history." },
  { name: "get_return_window", args: ["order_id"], desc: "Days remaining, and whether the item is excluded." },
  { name: "get_refund_quote", args: ["order_id", "reason"], desc: "What would be refunded, before anything is promised." },
  { name: "issue_refund", args: ["order_id", "amount", "caller_confirmed"], desc: "Refund to the original payment method." },
  { name: "send_replacement", args: ["order_id", "reason"], desc: "Ship a replacement instead of refunding." },
  { name: "get_refund_status", args: ["order_id"], desc: "Where an in-flight refund has got to." },
  { name: "apply_goodwill_credit", args: ["amount"], desc: "Store credit, capped by policy." },
  { name: "escalate_to_human", args: ["reason"], desc: "Hand the call to a person." },
];

const RULES = [
  "Never issue a refund outside the return window without a supervisor.",
  "Never read back more than the last four digits of a card.",
  "Verify identity before disclosing or changing anything on an account.",
  "An OTP must be read aloud by the caller — never inferred or guessed.",
  "Goodwill credit is capped at £25 per call.",
];

const SEED = [
  { name: "customers", rows: 240, note: "40 with saved cards, 12 guests" },
  { name: "orders", rows: 610, note: "18% delivered outside the window" },
  { name: "returns", rows: 95, note: "22 excluded items" },
  { name: "payments", rows: 480, note: "9 expired cards" },
  { name: "policies", rows: 12, note: "window, exclusions, credit cap" },
];

const STAGES = {
  understand: (source) => ({
    title: "Understanding the agent",
    steps: [
      think(`Reading ${source.value} — entrypoint, tool registry, prompt package.`),
      tool("read_source", "142 files · Python 3.12 · Dockerfile, db/schema.sql"),
      think("Taking each tool's signature from the code rather than its name, so the arguments and their permitted values are exact."),
      tool("extract_tools", `${TOOLS.length} tools · 9 with required arguments`),
      json("issue_refund", "order_id: str · amount: float · caller_confirmed: bool (must be true)"),
      think("Separating rules the code enforces from rules only the prompt states — the second kind is where agents drift."),
      tool("extract_rules", `${RULES.length} hard rules · 2 enforced in code, 2 prompt-only, 1 found in prose`),
      file("contract.json", "tools, permitted values, hard rules, the real data"),
      note(
        "Contract written from the source, not from a form — nothing here was typed by hand. " +
        "Three of the five rules are not enforced anywhere in code, so the code will happily let the agent break them — those are worth the hardest scenarios. " +
        "One of the three was found in a README rather than the prompt, and a README is writable by anything in the repo, so it is recorded with its origin and held back until you accept it.",
      ),
    ],
    chips: ["build the world →", "show me the tools"],
  }),

  build: () => ({
    title: "Building the world its tools act on",
    steps: [
      think("The agent's tools have to hit something that answers truthfully, including a truthful refusal."),
      tool("write_handlers", `${TOOLS.length} handlers · one per tool`),
      think("Seeding what the use cases need, including the awkward rows — a world of happy customers proves nothing."),
      tool("seed_world", "1,437 rows across 5 tables"),
      json("seeded_edges", "18% of orders outside the window · 22 excluded items · 9 expired cards · 12 guests"),
      tool("write_checks", "12 sub-goals · each check written as code in checks/<goal>.py"),
      file("world/handlers.py", "answers every tool call from real state"),
      note(
        "The world is up and resettable. Grading is settled from world state and every tool call, " +
        "so a scenario asserts the order is actually cancelled rather than that the agent said so.",
      ),
    ],
    chips: ["write the scenarios →", "what did you seed?"],
  }),

  scenarios: () => ({
    title: "Proving scenarios",
    steps: [
      think("One scenario per real use case, each with its own persona brief and sub-goals."),
      tool("draft_scenarios", "8 drafted across the use-case range"),
      think("Three gates, all code, no model: ready, solvable, not vacuous."),
      tool("gate · ready", "8 / 8 the world holds what the scenario presumes"),
      tool("gate · solvable", "8 / 8 the reference solution passes the scenario's own checks"),
      tool("gate · not vacuous", "7 / 8 running nothing must fail the checks"),
      note(
        "One scenario failed the third gate and was rewritten, not kept: its identity check asserted " +
        "\"no modification happened before authentication\", which is trivially true when nothing happened. " +
        "A check that passes while the agent did nothing grades nothing while reporting a result.",
      ),
      tool("gate · not vacuous", "8 / 8 after the rewrite"),
      file("scenarios/", "one folder each: scenario.json, setup.py, ready.py, checks/"),
      note("8 of 8 kept. Only proved scenarios are ever run."),
    ],
    chips: ["use this environment →", "write 4 more edge cases"],
  }),
};

const ASKS = [
  {
    match: /tool|argument|permitted/i,
    steps: [
      json("tools", TOOLS.map((t) => `${t.name}(${t.args.join(", ") || ""})`).join(" · ")),
      note("Read from the source, so the argument names and permitted values are the agent's real ones — that is what a hand-typed list could never give you."),
    ],
  },
  {
    match: /seed|data|world/i,
    steps: [
      json("seeded", SEED.map((s) => `${s.name} ${s.rows}`).join(" · ")),
      note("The distribution is the test: 18% of orders sit outside the return window, 22 items are excluded, 9 cards have expired."),
    ],
  },
  {
    match: /rule|guardrail|policy/i,
    steps: [
      json("hard_rules", RULES.join(" · ")),
      note("Two are enforced in code. Two more exist only in the prompt, which is why they are graded rather than guaranteed — and one was read out of prose, so nothing grades against it until you confirm it."),
    ],
  },
  {
    match: /more (scenario|edge|case)/i,
    steps: [
      think("Reading the 8 that exist first so I do not repeat them."),
      tool("draft_scenarios", "4 drafted · abusive caller, double refund attempt, wrong order id, silence mid-call"),
      tool("gates", "4 / 4 passed ready, solvable and not vacuous"),
      note("Four added. The double-refund one is the interesting failure — it needs the agent to notice a refund is already in flight."),
    ],
  },
];

export const builderRun = (stage, source, text = "") => {
  if (stage === "ask") {
    const hit = ASKS.find((a) => a.match.test(text));
    if (hit) return { title: null, steps: hit.steps, chips: [] };
    return {
      title: null,
      steps: [note("In this prototype I answer on tools, seeded data, rules and adding scenarios — and the stage buttons drive the rest.")],
      chips: [],
    };
  }
  return STAGES[stage](source);
};

/** What the builder hands to the workspace when the user accepts it. */
export const derivedEnvironment = (source) => ({
  id: "env-returns-line",
  agentType: "voice_platform",
  name: "Returns & refunds line",
  surface: "voice",
  domain: "support",
  tagline: "Read from your agent",
  description:
    "Phone support for an online storefront. Callers chase deliveries, ask for refunds and dispute charges; the agent resolves inside policy or hands over.",
  difficulty: "Advanced",
  popularity: 1,
  builtFrom: source,
  seed: { tables: SEED },
  tools: TOOLS.map((t) => ({ name: t.name, desc: t.desc, args: t.args })),
  rules: RULES,
  evalPreset: ["task_success", "policy_adherence"],
});
