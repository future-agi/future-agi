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
import { argsSignatureLine } from "./rlContract";
import { worldFor, worldToolsOf, seededRowsOf } from "./agentWorlds";
import { proofKindOf } from "./rsiModel";

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
  voice: ["repo", "platform", "mcp", "upload"],
  chat: ["repo", "mcp", "upload"],
  cua: ["repo", "upload"],
  coding: ["repo", "mcp", "upload"],
  physical: ["repo", "upload"],
  custom: ["repo", "platform", "mcp", "upload"],
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
    id: "platform",
    label: "Hosted agent platform",
    blurb: "Vapi, Retell, Bland, ElevenLabs — the agent lives on their platform, not in a repo you can point at.",
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

/* The tools a build is narrating: the source's own when it carries them (a
   template, a twin, a scratch build), else the world read from the agent. */
const toolsOfSource = (source) => (Array.isArray(source?.tools) && source.tools.every((t) => t && typeof t === "object")
  ? source.tools
  : worldToolsOf(worldFor(source), source));

/*
  How the agent is read, which decides what the narration may claim. Only a
  repository or an uploaded bundle has code, files and READMEs to read; a
  platform agent is read from its assistant config; a prompt from the
  prompt; an agent we can only call from its record and conversations.
*/
const readKindOf = (source) => {
  const k = source?.kind;
  if (k === "repo" || k === "upload") return "code";
  if (k === "template" || k === "twin") return "world";
  if (k === "platform" && (source.agentMode === "prompt" || source.provider === "builtin" || source.provider === "other")) return "prompt";
  if (k === "platform") return "platform";
  return "endpoint";
};

/* Sealed, live or prompt only — the agent's own proof kind when it's known
   (RSI PRD, Sealed and live environments), else what its source implies. */
const proofOf = (source) => {
  if (source?.connectedAgent) return proofKindOf(source.connectedAgent).kind;
  const read = readKindOf(source);
  if (read === "platform" || read === "endpoint") return "live";
  if (read === "prompt") return "prompt-only";
  return "sealed";
};

const providerLabel = (id) => PLATFORM_PROVIDERS.find((o) => o.value === id)?.label || "the platform";

/* "12 sub-goals · how" — the distinct named sub-goals the scenarios use, when
   the caller knows them; never a fixed number. */
const subGoalLine = (source, how) => {
  const n = source?.subGoalCount;
  return n ? `${n} sub-goal${n === 1 ? "" : "s"} · ${how}` : `one check per sub-goal · ${how}`;
};

const STAGES = {
  understand: (source) => {
    const read = readKindOf(source);
    const tools = toolsOfSource(source);
    const world = worldFor(source);
    const ws = source?.worldSource;
    const who = source?.agentName || source?.connectedAgent?.name || "the agent";
    const sample = tools.find((t) => t.name === world.sampleTool) || tools.find((t) => t.args?.length) || tools[0];
    const opening = {
      code: [
        think(`Reading ${source.value || source.repoUrl || "the agent"} — entrypoint, tool registry, prompt package.`),
        tool("read_source", "142 files · Python 3.12 · Dockerfile, db/schema.sql"),
        think("Taking each tool's signature from the code rather than its name, so the arguments and their permitted values are exact."),
      ],
      platform: [
        think(`Reading the assistant config from ${providerLabel(source.provider)} — the prompt, tools and settings ${who} runs with.`),
        tool("read_config", `${providerLabel(source.provider)} assistant ${source.agentId || source.value || ""}`.trim()),
      ],
      prompt: [
        think(`Reading ${who}'s prompt — the instructions it follows and the tools it names.`),
        tool("read_prompt", "system prompt"),
      ],
      world: [
        think(`Reading ${source.value || "the world"} — its tools, tables and rules.`),
      ],
      endpoint: [
        think(`We can only call ${who}, so we read its record and recent conversations — not its code.`),
        tool("read_record", "agent record · recent conversations"),
      ],
    }[read];
    const fromWorld = ws?.kind === "template"
      ? [think(`Starting from the ${ws.label} world — its tables, tools and rules as designed — and checking the agent's own tools against it.`)]
      : ws?.kind === "mcp"
        ? [tool("list_tools", `${ws.label} · the MCP server's tools become the world's`)]
        : [];
    const rules = read === "code"
      ? [
        think("Separating rules the code enforces from rules only the prompt states — the second kind is where agents drift."),
        tool("extract_rules", `${world.rules.length} hard rules · 2 enforced in code, 2 prompt-only, 1 found in prose`),
        file("contract.json", "tools, permitted values, hard rules, the real data"),
        note(
          "Contract written from the source, not from a form — nothing here was typed by hand. " +
          "Three of the five rules are not enforced anywhere in code, so the code will happily let the agent break them — those are worth the hardest scenarios. " +
          "One of the three was found in a README rather than the prompt, and a README is writable by anything in the repo, so it is recorded with its origin and held back until you accept it.",
        ),
      ]
      : [
        tool("extract_rules", `${world.rules.length} hard rules · ${read === "world" || ws?.kind === "template" ? "as the world states them" : "read from the prompt"}`),
        note(
          "Contract written from what we read, not from a form — correct anything here in chat. " +
          "Rules stated only in a prompt are graded rather than guaranteed, so they get the hardest scenarios.",
        ),
      ];
    return {
      title: "Understanding the agent",
      steps: [
        ...opening,
        ...fromWorld,
        tool("extract_tools", tools.length
          ? `${tools.length} tools${read === "code" && !source?.tools ? " · 9 with required arguments" : ""}`
          : "no tools yet — the world will answer whatever the agent calls"),
        /* One signature, read the way the Contract tab lists it — names, types, permitted values. */
        sample ? json(sample.name, argsSignatureLine(sample) || "no arguments") : json("tools", "none read yet"),
        ...rules,
      ],
      chips: ["build the world →", "show me the tools"],
    };
  },

  build: (source) => {
    const world = worldFor(source);
    const tools = toolsOfSource(source);
    const proof = proofOf(source);
    const seeded = tool("seed_world", `${seededRowsOf(world).toLocaleString()} rows across ${world.seed.length} tables`);
    /* Live: the agent's tools reach the customer's real systems, so only the
       starting state is checked and a model judges sub-goals (RSI PRD). */
    if (proof === "live") {
      return {
        title: "Staging the starting state",
        steps: [
          think("This agent runs live in your account, so its tools reach your real systems — the world stages each scenario's starting state; it doesn't answer the tools."),
          seeded,
          json("seeded_edges", world.edges),
          tool("write_checks", subGoalLine(source, "judged by a model from the conversation")),
          note("Live: only the starting state is checked, and a model judges each sub-goal from the conversation. Results from this environment are labelled Live."),
        ],
        chips: ["write the scenarios →", "what did you seed?"],
      };
    }
    return {
      title: "Building the world its tools act on",
      steps: [
        think("The agent's tools have to hit something that answers truthfully, including a truthful refusal."),
        tool("write_handlers", tools.length
          ? `${tools.length} handlers · one per tool`
          : "no handlers yet — written when an agent's tools are read"),
        think("Seeding what the use cases need, including the awkward rows — a world of happy customers proves nothing."),
        seeded,
        json("seeded_edges", world.edges),
        tool("write_checks", subGoalLine(source, "each checked against the world's end state and the agent's tool calls")),
        ...(readKindOf(source) === "code" ? [file("world/handlers.py", "answers every tool call from real state")] : []),
        note(proof === "prompt-only"
          ? `The world is up and resettable. The prompt runs on our model without your own tools, so a scenario asserts ${world.proof} in this world — results are labelled Prompt only.`
          : "The world is up and resettable. In a sealed environment grading is settled from world state and every tool call, " +
            `so a scenario asserts ${world.proof} rather than that the agent said so.`),
      ],
      chips: ["write the scenarios →", "what did you seed?"],
    };
  },

  scenarios: (source) => {
    /* A count set on the connect form is honoured exactly; with none the
       builder drafts across the whole use-case range. */
    const n = source?.scenarioCount || source?.draftCount || 8;
    /* A prebuilt world's scenarios were proven when the world was made; they
       come over as they are — never rewritten to suit the agent. */
    if (source?.fixedWorld)
      return {
        title: "Taking the world's scenarios",
        steps: [
          think(`These are the ${source.fixedWorld} world's own ${source.draftCount ?? n} scenarios, proven when the world was made — the same tests its sample agent runs.`),
          tool("copy", `${source.draftCount ?? n} scenarios · unchanged · same checks and reference outcomes`),
          note("They aren't rewritten for your agent, so what counts as success stays the world's, and results compare with the sample agent's."),
        ],
        chips: ["use this environment →"],
      };
    /* Each scenario is checked as it's written, not all at the end — so the
       narration reports checks as they land, and the scenario a check sends
       back (the third one written, when there are enough) is rewritten and
       re-checked before the next is kept. The Scenarios table shows the same
       thing row by row: Checking…, then Proved. */
    const third = Math.min(3, n);
    const early = Math.max(1, Math.min(2, n - 1));
    const mid = Math.max(third, Math.round(n * 0.6));
    /* The three validity checks are a sealed environment's (RSI PRD); a live
       or prompt-only one checks that the starting state is ready. */
    const sealed = proofOf(source) === "sealed";
    const checks = sealed
      ? [
        think("Each one is checked as soon as it's written — three gates, all code, no model: ready, solvable, not vacuous — and kept only once it passes."),
        tool("write + check", `${early} of ${n} kept · each passed ready, solvable and not vacuous`),
        tool("gate · not vacuous", `scenario ${third} failed — running nothing still passed its checks`),
        note(
          `Scenario ${third} was sent back, not kept: its identity check asserted ` +
          "\"no modification happened before authentication\", which is trivially true when nothing happened. " +
          "A check that passes while the agent did nothing grades nothing while reporting a result.",
        ),
        tool("rewrite + re-check", `scenario ${third} rewritten · all three gates pass · kept`),
      ]
      : [
        think("Each one is checked as soon as it's written — its starting state has to be ready before it's kept."),
        tool("write + check", `${early} of ${n} kept · starting state ready`),
        tool("check · ready", `scenario ${third} failed — it presumed a record the starting state doesn't hold`),
        tool("rewrite + re-check", `scenario ${third} rewritten · starting state ready · kept`),
      ];
    return ({
      title: "Proving scenarios",
      steps: [
        think(source?.scenarioCount
          ? `Writing exactly ${n} — the number you asked for — spread across rules, traps, adversarial callers and edge cases.`
          : `Writing ${n}, one per real use case, each with its own persona brief and sub-goals.`),
        ...checks,
        tool("write + check", `${mid} of ${n} kept`),
        tool("write + check", `${n} of ${n} kept · 1 rewritten`),
        ...(readKindOf(source) === "code" ? [file("scenarios/", "one folder each: scenario.json, setup.py, ready.py, checks/")] : []),
        note(`${n} of ${n} kept, each proved as it was written. Only proved scenarios are ever run.`),
      ],
      chips: ["use this environment →", "write 4 more edge cases"],
    });
  },
};

const asksOf = (world, source) => [
  {
    match: /tool|argument|permitted/i,
    steps: [
      json("tools", toolsOfSource(source).map((t) => `${t.name}(${(t.args || []).join(", ")})`).join(" · ")),
      note(readKindOf(source) === "code"
        ? "Read from the source, so the argument names and permitted values are the agent's real ones — that is what a hand-typed list could never give you."
        : "Read from what the agent exposes — correct any of them here in chat."),
    ],
  },
  {
    match: /seed|data|world/i,
    steps: [
      json("seeded", world.seed.map((t) => `${t.name} ${t.rows}`).join(" · ")),
      note(`The distribution is the test: ${world.edges}.`),
    ],
  },
  {
    match: /rule|guardrail|policy/i,
    steps: [
      json("hard_rules", world.rules.join(" · ")),
      note(readKindOf(source) === "code"
        ? "Two are enforced in code. Two more exist only in the prompt, which is why they are graded rather than guaranteed — and one was read out of prose, so nothing grades against it until you confirm it."
        : "Rules stated only in a prompt are graded rather than guaranteed."),
    ],
  },
  {
    /* Asked about, not asked for — "write 5 more scenarios" is handled
       before this and proposes them for real. */
    match: /more (scenario|edge|case)/i,
    steps: [
      note("Ask me to write them — for example “write 5 more scenarios” — and I'll propose them for review on the Scenarios tab."),
    ],
  },
];

/*
  Built from a description: there's no agent to read and no data to seed
  yet, so the narration says what it took from the description and what's
  still to come — with this environment's own counts, never a demo world's.
  source = { kind: "scratch", rules, scenarios, kept, surface }
*/
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const SCRATCH_STAGES = {
  understand: (source) => ({
    title: "Reading your description",
    steps: [
      think("Reading what you wrote — who calls, what the agent does, when it's done and when it hands off."),
      tool("extract_rules", `${plural(source.rules || 0, "rule")} · from your description`),
      tool("extract_tools", "none yet — the agent's tools are read when you connect it"),
      note("Rules come from your words, so they're graded from the conversation until the agent is connected. Correct any of them here in chat."),
    ],
    chips: [],
  }),
  build: (source) => ({
    title: "Setting up the world",
    steps: [
      think(`A ${source.surface === "voice" ? "voice" : "chat"} world, empty for now: no code to read until an agent is connected, and no data to seed.`),
      tool("write_checks", "one check per sub-goal · judged from the conversation"),
      note("Connect your agent from Overview. Its tools are read then, and the world learns to answer them. The world starts with no data."),
    ],
    chips: [],
  }),
  scenarios: (source) => ({
    title: "Writing scenarios",
    steps: [
      think("One per situation your description implies — the flow itself, edge cases, callers who push, and tools that fail."),
      tool("write + check", `${source.kept ?? source.scenarios ?? 0} of ${source.scenarios ?? 0} kept · each has a reference outcome and sub-goals`),
      note("They run once your agent is connected."),
    ],
    chips: [],
  }),
};

export const builderRun = (stage, source, text = "") => {
  if (source?.kind === "scratch" && SCRATCH_STAGES[stage]) return SCRATCH_STAGES[stage](source);
  if (stage === "ask") {
    const hit = asksOf(worldFor(source), source).find((a) => a.match.test(text));
    if (hit) return { title: null, steps: hit.steps, chips: [] };
    return {
      title: null,
      steps: [note("Ask about tools, data or rules, or ask for more scenarios.")],
      chips: [],
    };
  }
  return STAGES[stage](source);
};

/** What the builder hands to the workspace when the user accepts it. */
/* How the agent is reached follows what was connected: a voice platform or
   voice repo stays voice, an HTTP endpoint or an MCP server is a chat agent. */
const REACH_BY_MODALITY = {
  voice: { agentType: "voice_platform", surface: "voice" },
  chat: { agentType: "chat_webhook", surface: "chat" },
  coding: { agentType: "mcp_agent", surface: "chat" },
};

/* An agent that only talks one way (its Type on the agent page) is built for that way. */
const soleChannelOf = (source) => {
  const ch = source?.connectedAgent?.values?.channels;
  return Array.isArray(ch) && ch.length === 1 ? ch[0] : null;
};

const agentLabelOf = (source) => String(source?.agentName || source?.name || "").trim();

/**
 * What the builder hands to the workspace when the user accepts it.
 *
 * Every build is its own environment — `id` is minted per build by the
 * caller. (A fixed id made every build the same environment and overwrote
 * the seeded demo that shares it.) The seeded demo passes its own id.
 */
export const derivedEnvironment = (source, { id } = {}) => {
  /* The world the agent's own tools act on — read from what it is. */
  const world = worldFor(source);
  return {
    id: id || "env-returns-line",
    /* A chat agent on a voice platform (Vapi chat, ElevenLabs chat) is
       still reached in chat — what it was connected as decides, before the
       platform's usual modality. */
    ...(REACH_BY_MODALITY[source?.channel] || REACH_BY_MODALITY[soleChannelOf(source)] || REACH_BY_MODALITY[source?.agentType] || REACH_BY_MODALITY[detectedStack(source).modality] || REACH_BY_MODALITY.voice),
    /* Named by whoever started the build (the agent page asks); otherwise after
       the agent it tests. The demo name only when nothing is known. */
    name: source?.envName || (agentLabelOf(source) ? `${agentLabelOf(source)} environment` : "Returns & refunds line"),
    domain: world.domain,
    tagline: "Read from your agent",
    description: world.description,
    difficulty: "Advanced",
    popularity: 1,
    builtFrom: source,
    seed: { tables: world.seed },
    tools: worldToolsOf(world, source),
    rules: world.rules,
    evalPreset: ["task_success", "policy_adherence"],
  };
};
