// Run-source catalog (R4-SPEC D2–D5, D12, D20, D23; implementation; frozen API).
// One data source for the New experiment picker, leaderboard logos, the
// provenance line and the snippets. Pure: no React, no clock, no random.
//
//   import { RUN_SOURCE_CARDS, catalogEntry, cardLogos, catalogCount,
//     modesFor, graphQualityOf } from "src/sections/common/RunSources/catalog";
import {
  BADGES,
  ENDPOINTS,
  EXECUTION_MODES,
  FRAMEWORKS,
  HARNESSES,
  KIND_MODES,
  OTEL_PREVIEW_REASON,
  RUNNING,
} from "./catalogEntries";
import { isSurfaceShown } from "src/launch/launchSwitch";
import {
  LOADING_AVAILABILITY,
  NOT_IN_RELEASE_REASON,
  RUNNING_SOURCE_KINDS,
  pickReason,
  runningCardReason,
  sourceAvailable,
  sourceUnavailableReason,
} from "./runSourceAvailability";

// L6 launch switch (picker:step1-heading, picker:search,
// picker:search-no-match): a launch build offers only the step-1 cards dev
// runs (Prompt; Agent graph and Already running once a lane ships them), and
// its copy names only those.
const CARD_SURFACE = Object.freeze({
  agent: "exp.new.kindMenu",
  running: "exp.new.runningSources",
});
/** Whether a step-1 card shows in this build. */
export const stepCardShown = (id) =>
  !CARD_SURFACE[id] || isSurfaceShown(CARD_SURFACE[id]);
const runningShown = () => stepCardShown("running");

export { BADGES, EXECUTION_MODES, KIND_MODES, OTEL_PREVIEW_REASON };
export { pickReason, sourceAvailable, sourceUnavailableReason };

// Server support is independent of launch visibility. `availability`
// (runSourceAvailability.js, the server's report) decides what this
// workspace can pick. Nothing the launch shows is hidden by it: the Already
// running card and every tile behind it stay on screen, and a source the
// server cannot run (or has not answered for yet) is greyed with its reason
// (pickReason, runningCardReason). Copy, counts and suggestions name only
// what can be picked. Functions that take it read the whole launch catalog
// when it is omitted (lookups, names of existing runs).
const narrowed = (availability) => availability !== undefined;

/** The Already running kinds offered: launch-shown and server-run. */
export const runningKinds = (availability) =>
  runningShown()
    ? RUNNING_SOURCE_KINDS.filter(
        (kind) =>
          !narrowed(availability) || sourceAvailable(kind, availability),
      )
    : [];

// How copy names each Already running kind.
const RUNNING_WORDS = Object.freeze({
  http: {
    one: "an endpoint",
    many: "endpoints",
    hosted: "an HTTP endpoint",
    called: "an HTTP endpoint",
  },
  a2a: {
    one: "an A2A agent",
    many: "A2A agents",
    hosted: "an A2A agent",
    called: "an A2A agent",
  },
  mcp_tool: {
    one: "an MCP tool",
    many: "MCP tools",
    hosted: "an MCP tool",
    called: "an MCP server tool",
  },
});

/** `a`, `a or b`, `a, b or c` (pure). */
export const orList = (words) =>
  words.length > 1
    ? `${words.slice(0, -1).join(", ")} or ${words[words.length - 1]}`
    : words[0] ?? "";

/**
 * The offered Already running kinds in words: `one` (`an endpoint`) or
 * `many` (`endpoints`).
 */
export const runningWords = (availability, form = "one") =>
  runningKinds(availability).map((kind) => RUNNING_WORDS[kind][form]);

/**
 * Why a step-1 card cannot be picked here, or null: the server's reason for
 * Prompt or Agent graph; for Already running, a reason only when the server
 * runs none of its kinds (runningCardReason); for a held card (Your code,
 * Agent harness, Voice agent) always the release line.
 */
export function cardUnavailableReason(card, availability) {
  if (!card) return null;
  if (card.held) return NOT_IN_RELEASE_REASON;
  if (card.subTiles) return runningCardReason(availability);
  return sourceUnavailableReason(card.kind, availability);
}

/**
 * Whether copy may point at Agent graph: the build shows it and the server
 * has not refused it (its card then shows the server's reason instead).
 */
export const agentOffered = (availability) =>
  stepCardShown("agent") && !sourceUnavailableReason("agent", availability);

// How copy names the kinds that run in Future AGI.
const IN_APP_WORDS = Object.freeze({
  prompt: { one: "a prompt", many: "prompts" },
  agent: { one: "an agent graph", many: "agent graphs" },
});

/**
 * The offered kinds that run in Future AGI, in words: a prompt, and an
 * agent graph where copy may point at it (agentOffered). `one` or `many`.
 */
export const inAppWords = (availability, form = "one") => [
  IN_APP_WORDS.prompt[form],
  ...(agentOffered(availability) ? [IN_APP_WORDS.agent[form]] : []),
];

/**
 * Every way to run that New experiment offers here, for copy outside step 1
 * (its hover, the Edit experiment helper): `prompts and agent graphs`, or
 * `prompts, agent graphs or endpoints` once the server runs an Already
 * running source. Built from the server's report only.
 */
export function offeredWays(availability) {
  const inApp = inAppWords(availability, "many");
  const running = runningWords(availability, "many");
  return running.length ? orList([...inApp, ...running]) : inApp.join(" and ");
}

/**
 * The Edit experiment helper under the drawer title (RunExperiment): the
 * ways to run this workspace can pick, never a source the server cannot
 * run. Unlike the lookups, a missing report reads as still loading.
 * @param {{datasetName?: string, availability?: object}} o
 */
export function editHelpOf({
  datasetName,
  availability = LOADING_AVAILABILITY,
} = {}) {
  return `Run ${offeredWays(availability)} on ${
    datasetName || "this dataset"
  }. Every run is scored by the same evals.`;
}

/** The step-1 heading's tooltip: what runs where, for what is offered. */
export function stepDisambiguation(availability) {
  const running = runningWords(availability);
  const inApp = agentOffered(availability)
    ? "Prompt and Agent graph run in Future AGI"
    : "A prompt runs in Future AGI";
  return running.length
    ? `${inApp}. Already running = ${orList(running)} you host.`
    : `${inApp}, on every row.`;
}

/** Step 1 copy (D2, verbatim). `{n}` = catalogCount(). */
export const STEP1_COPY = Object.freeze({
  title: "What are you testing?",
  // IT-R1 G1 P8 (was `Pick one. Add another way later to see which wins.`)
  subtitle: "Add one or more ways to run. Add a second to compare them.",
  get disambiguation() {
    return stepDisambiguation();
  },
  // L7c g2 (picker:search): the count is every way step 1 offers (Prompt,
  // Agent graph and each Already running entry, stepWayCount()); prompts,
  // graphs and their models are searched too.
  searchPlaceholder: (n, availability) => {
    const graphs = agentOffered(availability) ? "graphs, " : "";
    if (runningKinds(availability).length)
      return `Search ${n} ways to run, prompts, ${graphs}models`;
    return graphs
      ? "Search prompts, graphs, models"
      : "Search prompts and models";
  },
  /** The line under a search that shows only the first 12 of `total`. */
  truncated: (shown, total) => `${shown} of ${total} · keep typing`,
});

/**
 * FE-0 (dataset integration, owner Q12): a source is offered only when it has a real
 * path. Your code and Agent harness printed generated runner scripts for
 * routes that do not exist, and Upload results printed an upload script;
 * they are absent until the SDK's `futureagi run TARGET` rows are green
 * (FE-1). Traces I already have is a mock with no backend: absent until
 * SRV-13 (DESIGN rev2 §5.1, P5). Framework and harness entries stay as
 * lookup data so existing runs keep their names and logos.
 */
const ABSENT_RUNNING = new Set(["upload", "traces"]);
const OFFERED_RUNNING = RUNNING.filter((e) => !ABSENT_RUNNING.has(e.id));

// Traces I already have
// and Upload results are listed behind Already running, greyed with
// NOT_IN_RELEASE_REASON, and never offered. They stay out of ALL, CATALOG,
// entriesFor, isOffered and searchCatalog, so no click, key, search Enter or
// link can add one.
const HELD_RUNNING = Object.freeze(
  RUNNING.filter((e) => ABSENT_RUNNING.has(e.id)).map((e) =>
    Object.freeze({ ...e, held: true }),
  ),
);

/** The held entries listed, greyed, behind a card (`running` only). */
export const heldEntriesFor = (cardId) =>
  cardId === "running" && runningShown() ? HELD_RUNNING : [];

/**
 * Why a tile cannot be picked, or null: the release line for a held entry,
 * else pickReason (the server's report).
 */
export const tileUnavailableReason = (entry, availability) =>
  entry?.held ? NOT_IN_RELEASE_REASON : pickReason(entry?.kind, availability);

/** Catalog groups (level 2), by kind: only what the picker offers. */
export const CATALOG = Object.freeze({
  endpoints: ENDPOINTS,
  running: OFFERED_RUNNING,
});

/** Level-2 group headers. */
export const GROUP_LABELS = Object.freeze({
  python_auto: "Python · traced automatically",
  ts_auto: "TypeScript · traced automatically",
  own_tracing: "Bring your own tracing",
  harness: "Agent harnesses",
  endpoint: "Endpoints",
  running: "Already running",
});

/** Every entry the picker offers, in catalog order. */
const ALL = Object.freeze([...ENDPOINTS, ...OFFERED_RUNNING]);
// Lookups also know framework and harness entries (existing runs' names).
const BY_ID = Object.fromEntries(
  [...FRAMEWORKS, ...HARNESSES, ...ENDPOINTS, ...RUNNING].map((e) => [e.id, e]),
);

/** Every offered entry, in catalog order. */
export const allEntries = () => ALL;

/** Picker visibility follows the card gate; the server's report gates picks. */
export const isOffered = (id) => runningShown() && ALL.some((e) => e.id === id);

/** One entry by id, or null. */
export const catalogEntry = (id) => (id ? BY_ID[id] ?? null : null);

// The entries of `list` the server can run (all of them with no report).
const runnable = (list, availability) =>
  narrowed(availability)
    ? list.filter((e) => sourceAvailable(e.kind, availability))
    : list;

// Card id -> the catalog groups behind it.
const CARD_ENTRIES = {
  prompt: [],
  agent: [],
  running: [...ENDPOINTS, ...OFFERED_RUNNING],
};

/**
 * Entries behind a card (`running`) or of a run kind (`http`, `traces`),
 * narrowed to what the server can run when `availability` is passed.
 * `prompt` / `agent` have none.
 * @param {string} kind
 * @param {object} [availability]  runSourceAvailability.js
 */
export function entriesFor(kind, availability) {
  if (!runningShown()) return [];
  if (CARD_ENTRIES[kind]) return runnable(CARD_ENTRIES[kind], availability);
  return runnable(
    ALL.filter((e) => e.kind === kind),
    availability,
  );
}

const CARD_LOGO_IDS = {};

/**
 * The logos a card names and `more` = every entry behind the card (custom
 * ones counted) minus the logos named.
 * `total` is every entry behind the card, so a card that shows `k` logos
 * prints `+{total - k}` (options-truth L1 B: harness 4 + `+4` = 8).
 * @param {string} kind  card id
 * @returns {{logos: object[], more: number, total: number}}
 */
export function cardLogos(kind) {
  const ids = CARD_LOGO_IDS[kind];
  const total = (CARD_ENTRIES[kind] ?? []).length;
  if (!ids) return { logos: [], more: 0, total };
  const logos = ids.map(catalogEntry);
  return { logos, more: Math.max(0, total - logos.length), total };
}

/** `+N` of a card that shows `shown` logos: every other entry behind it. */
export const moreCount = (card, shown) =>
  Math.max(0, Number(card?.total ?? 0) - Number(shown ?? 0));

/** Every visible catalog entry, including unavailable source cards. */
export const catalogCount = () => ALL.length;

const fill = (text, values) =>
  text.replace(/\{(\w+)\}/g, (_, k) => String(values[k] ?? ""));

/** The step-1 cards (D2 copy; counts computed). */
export const RUN_SOURCE_CARDS = Object.freeze(
  [
    {
      id: "prompt",
      kind: "prompt",
      title: "Prompt",
      icon: "mdi:text-box-outline",
      line: "A prompt version and a model, with tools.",
      tip: "Runs a saved prompt version on every row, in Future AGI.",
    },
    {
      id: "agent",
      kind: "agent",
      title: "Agent graph",
      icon: "mdi:graph-outline",
      line: "A graph you built in Agent Playground.",
      tip: "Runs an Agent Playground graph on every row, in Future AGI.",
    },
    {
      id: "running",
      kind: "http",
      title: "Already running",
      icon: "mdi:play-network-outline",
      line: "An endpoint, an A2A agent, or an MCP tool.",
      tip: "Call an HTTP endpoint, an A2A agent or an MCP server tool per row.",
      // The kinds the server's report covers behind this card (one list).
      subTiles: RUNNING_SOURCE_KINDS,
    },
  ].map((card) => {
    // IT-R4 G1 P8: short lines (screen 07 word budget); the logos name the
    // frameworks and providers.
    const { logos, more, total } = cardLogos(card.id);
    return Object.freeze({
      ...card,
      line: fill(card.line, { more }),
      logoIds: logos.map((e) => e.id),
      more,
      total,
    });
  }),
);

const capitalized = (text) => text.charAt(0).toUpperCase() + text.slice(1);

/**
 * A card by id (`prompt`, `running`, …), or null for an unknown id. The
 * Already running card is always returned with every kind behind it (its
 * tiles all show; refused ones are greyed). With `availability`, its line
 * and tip name only the kinds the server runs; when it runs none, the card
 * keeps its own words and cardUnavailableReason greys it.
 * @param {string} id
 * @param {object} [availability]  runSourceAvailability.js
 */
export function runSourceCard(id, availability) {
  const card = RUN_SOURCE_CARDS.find((c) => c.id === id) ?? null;
  if (!card?.subTiles || !narrowed(availability)) return card;
  const kinds = card.subTiles.filter((kind) =>
    sourceAvailable(kind, availability),
  );
  if (!kinds.length || kinds.length === card.subTiles.length) return card;
  const words = (form) =>
    orList(kinds.map((kind) => RUNNING_WORDS[kind][form]));
  return Object.freeze({
    ...card,
    kind: kinds[0],
    line: `${capitalized(words("hosted"))} you host.`,
    tip: `Call ${words("called")} per row.`,
  });
}

// The logos a held card names (`+N` = the rest of its lookup entries).
const HELD_CARD_LOGOS = Object.freeze({
  code: {
    ids: ["langgraph", "crewai", "openai_agents", "llamaindex"],
    all: FRAMEWORKS,
  },
  harness: {
    ids: ["claude_code", "codex_cli", "gemini_cli", "openhands"],
    all: HARNESSES,
  },
});

/**
 * The step-1 cards this release does not
 * build. Shown after the offered cards (gated with Already running),
 * greyed with NOT_IN_RELEASE_REASON (cardUnavailableReason), and never
 * offered: runSourceCard, cardsForKind, orderedCards, stepWayCount and
 * stepWayNames leave them out, so no key, menu, deep link, search Enter or
 * copy opens or counts one. No `tip`: a held card names what it is (its
 * line, in its tooltip) and never prints a command. Titles, icons and lines
 * are the step-1 cards FE-0 removed; Your code names the SDK and CLI.
 */
export const HELD_SOURCE_CARDS = Object.freeze(
  [
    {
      id: "code",
      kind: "code",
      title: "Your code (SDK / CLI)",
      icon: "mdi:code-braces",
      line: "Your Python or TypeScript agent, any framework.",
    },
    {
      id: "harness",
      kind: "harness",
      title: "Agent harness",
      icon: "mdi:console",
      line: "Claude Code, Codex CLI and more, with your tools.",
    },
    {
      id: "voice",
      kind: "voice",
      title: "Voice agent",
      icon: "mdi:phone-in-talk",
      line: "Simulated callers talk to your agent.",
    },
  ].map((card) => {
    const logo = HELD_CARD_LOGOS[card.id];
    const logoIds = logo ? logo.ids.filter((id) => catalogEntry(id)) : [];
    const total = logo ? logo.all.length : 0;
    return Object.freeze({
      ...card,
      held: true,
      logoIds,
      more: Math.max(0, total - logoIds.length),
      total,
    });
  }),
);

/** The held cards this build shows: with Already running, after it. */
const heldCardsShown = () => (runningShown() ? HELD_SOURCE_CARDS : []);

/**
 * Any card step 1 draws, by id: an offered one (runSourceCard) or a held
 * one; null for an unknown id. Draw with it; pick only through
 * runSourceCard.
 */
export const stepCard = (id, availability) =>
  runSourceCard(id, availability) ??
  heldCardsShown().find((c) => c.id === id) ??
  null;

/**
 * The card that gets the `Recommended` tag for a dataset kind (at most one):
 * plain rows and conversations → Prompt; task suites → Agent graph.
 * @param {string} datasetKind  `task_suite` | `scenarios` | `rows` | …
 * @returns {string|null} card id
 */
const CARD_OF_KIND = {
  prompt: "prompt",
  agent: "agent",
  http: "running",
  a2a: "running",
  mcp_tool: "running",
  traces: "running",
};
const KIND_WORDS = {
  prompt: "a prompt",
  agent: "an agent graph",
  http: "an endpoint",
  a2a: "an A2A agent",
  mcp_tool: "an MCP server tool",
  traces: "linked traces",
};

/**
 * IT-R2 G1 P5: `Recommended` from context only (pure): the run being
 * re-tested, else the dataset's baseline run (`{card, reason}`), else null.
 * @param {{baseline?: {run_id, label}, results?: object[]}} runs  R1 body
 * @param {object} [retest]  the run being re-tested
 */
export function recommendedFromRuns(runs, retest = null) {
  // L7c g2 (picker:recommended): `You are re-testing #1A, a prompt` /
  // `Your baseline #1A is a prompt` (one sentence each).
  const pick = (run, lead) => {
    const kind = run?.kind ?? run?.config?.kind;
    const card = CARD_OF_KIND[kind];
    const label = String(run?.label ?? "").trim();
    return card ? { card, reason: lead(label, KIND_WORDS[kind]) } : null;
  };
  if (retest)
    return pick(retest, (label, words) =>
      label
        ? `You are re-testing ${label}, ${words}`
        : `You are re-testing ${words}`,
    );
  const id = runs?.baseline?.run_id;
  const base = id
    ? (runs?.results ?? []).find((r) => (r.run_id ?? r.id) === id)
    : null;
  return base
    ? pick(base, (label, words) =>
        label
          ? `Your baseline ${label} is ${words}`
          : `Your baseline is ${words}`,
      )
    : null;
}

// IT-R4 G1 P4: the first screen points at the right card. Card order by
// dataset kind; `Recommended` sits on the first card only.
const CARD_ORDER = Object.freeze({
  task_suite: ["agent", "prompt", "running"],
  conversations: ["prompt", "agent", "running"],
  rows: ["prompt", "agent", "running"],
  scenarios: ["prompt", "agent", "running"],
});
const KIND_REASON = Object.freeze({
  task_suite: "Recommended for task suites: run an agent graph on them",
  scenarios: "Recommended for scenarios: try a prompt on them first",
  conversations: "Recommended for conversations: try a prompt on them first",
  rows: "Recommended for rows: try a prompt on them first",
});

/**
 * The step-1 cards in the order for a dataset kind (pure): every card the
 * launch shows, whatever the server's report says (cardUnavailableReason
 * greys the ones it cannot run).
 */
export function cardsForKind(datasetKind, availability) {
  const order = CARD_ORDER[datasetKind] ?? CARD_ORDER.rows;
  return order
    .filter(stepCardShown)
    .map((id) => runSourceCard(id, availability))
    .filter(Boolean);
}

/**
 * L7c g2 (picker:recommended): the step-1 cards with the recommended card
 * first, so the `Recommended` badge is always on card 1 and keys 1–3 follow
 * what is shown (pure).
 * @param {string} datasetKind
 * @param {string|null} recommendedCard  card id
 * @param {object} [availability]  runSourceAvailability.js
 */
export function orderedCards(
  datasetKind,
  recommendedCard = null,
  availability,
) {
  const cards = cardsForKind(datasetKind, availability);
  const i = cards.findIndex((c) => c.id === recommendedCard);
  return i > 0
    ? [cards[i], ...cards.slice(0, i), ...cards.slice(i + 1)]
    : cards;
}

/**
 * The step-1 cards and their one `Recommended` (`{cards, rec}`): the door's
 * recommendation (`{card, reason}`) wins over the dataset kind's unless the
 * server cannot run that card here; the recommended card is first.
 * SourceCards draws these; keys 1–3 pick them. The held cards
 * (HELD_SOURCE_CARDS) follow, greyed: their keys pick nothing.
 */
export function stepCardsFor(datasetKind, recommended = null, availability) {
  const usable = (id) =>
    !narrowed(availability) ||
    cardsForKind(datasetKind, availability).some(
      (c) => c.id === id && !cardUnavailableReason(c, availability),
    );
  const rec =
    recommended?.card && usable(recommended.card)
      ? recommended
      : kindRecommendation(datasetKind, availability);
  return {
    cards: [
      ...orderedCards(datasetKind, rec?.card ?? null, availability),
      ...heldCardsShown(),
    ],
    rec,
  };
}

// Prompt and Agent graph where they can be picked: shown, and not refused.
const pickableCards = (availability) =>
  RUN_SOURCE_CARDS.filter(
    (c) =>
      !c.subTiles &&
      stepCardShown(c.id) &&
      !cardUnavailableReason(c, availability),
  );

/** Every way step 1 offers: Prompt, Agent graph and each running entry. */
export const stepWayCount = (availability) =>
  pickableCards(availability).length +
  (runningShown() ? runnable(ALL, availability).length : 0);

// Words that name a step-1 card (its title, what it is made of).
const CARD_ALIASES = Object.freeze({
  prompt: [
    "prompt",
    "prompt version",
    "template",
    "workbench",
    "llm",
    "model",
    "chat",
  ],
  agent: [
    "agent graph",
    "graph",
    "agent playground",
    "playground",
    "workflow",
    "node",
    "agent",
  ],
  running: [
    "already running",
    "running",
    "hosted",
    "my agent",
    "deployed",
    "live",
    "endpoint",
  ],
  // Held cards: found, then drawn greyed. No alias names another card's
  // words (prompt, graph, running, endpoint).
  code: ["your code", "code", "sdk", "cli", "python", "typescript", "function"],
  harness: [
    "agent harness",
    "harness",
    "coding agent",
    "claude code",
    "codex",
    "gemini cli",
  ],
  voice: ["voice agent", "voice", "phone call"],
});

/**
 * L7c g2 (picker:search): the step-1 cards a query names (pure). A card
 * matches its title or aliases, Prompt matches a saved prompt's name or
 * model, Agent graph a graph's name or a model its versions' nodes call.
 * Names match with or without separators (`gpt4o`, `GPT4o-mini` and
 * `4omini` all find `gpt-4o-mini`). `why` says what matched.
 * @param {string} q
 * @param {{prompts?: {name, model?}[], graphs?: {name, models?: string[]}[]}} [names]
 * @param {object} [availability]  runSourceAvailability.js
 * @returns {{card: string, why: string}[]}
 */
export function searchStepCards(
  q,
  { prompts = [], graphs = [] } = {},
  // Kept for callers: every card the launch shows can match (a refused one
  // is drawn greyed with its reason).
  _availability,
) {
  const has = matcherOf(q);
  if (!has) return [];
  const out = [];
  // The offered cards first, then the held ones (found, drawn greyed).
  [
    ...RUN_SOURCE_CARDS.filter((card) => stepCardShown(card.id)),
    ...heldCardsShown(),
  ].forEach((card) => {
    const words = [card.title, ...(CARD_ALIASES[card.id] ?? [])];
    let why = words.some(has) ? null : undefined;
    if (why === undefined && card.id === "prompt") {
      const p = prompts.find((x) => has(x?.name));
      const m = p ? null : prompts.find((x) => has(x?.model));
      if (p) why = `prompt ${p.name}`;
      else if (m) why = `model ${m.model} (${m.name})`;
    }
    if (why === undefined && card.id === "agent") {
      const g = graphs.find((x) => has(x?.name));
      const gm = g
        ? null
        : graphs.find((x) => (x?.models ?? []).some((model) => has(model)));
      if (g) why = `graph ${g.name}`;
      else if (gm)
        why = `model ${gm.models.find((model) => has(model))} (${gm.name})`;
    }
    if (why !== undefined) out.push({ card: card.id, why });
  });
  return out;
}

/**
 * The no-match suggestion: every way step 1 offers that can be picked, by
 * name (never a card the server refused).
 */
export const stepWayNames = (availability) => [
  ...pickableCards(availability).map((c) => c.title),
  ...(runningShown() ? runnable(ALL, availability).map((e) => e.name) : []),
];

/**
 * `{card, reason}` for the first card of a dataset kind (pure); with
 * `availability`, the first card the server can run.
 */
export function kindRecommendation(datasetKind, availability) {
  const cards = cardsForKind(datasetKind, availability);
  const first = narrowed(availability)
    ? cards.find((c) => !cardUnavailableReason(c, availability))
    : cards[0];
  // L6: a task suite without Agent graph gets the prompt reason.
  const reason =
    datasetKind === "task_suite" && first?.id !== "agent"
      ? KIND_REASON.rows.replace("rows", "task suites")
      : KIND_REASON[datasetKind] ?? KIND_REASON.rows;
  return first ? { card: first.id, reason } : null;
}

const MODE_BY_ID = Object.fromEntries(EXECUTION_MODES.map((m) => [m.id, m]));

/** The label of a mode for an entry (`Remote endpoint`, `This machine`). */
export function modeLabel(modeId, entryOrId) {
  const entry =
    typeof entryOrId === "string" ? catalogEntry(entryOrId) : entryOrId;
  if (modeId === "remote" && entry?.remoteLabel) return entry.remoteLabel;
  return MODE_BY_ID[modeId]?.label ?? modeId;
}

const UNAVAILABLE = {};

/**
 * Status of a mode for an entry (options-truth L1 C11): every place runs,
 * so this is always `today` (it never returns `preview`).
 */
function modeStatus() {
  return "today";
}

/**
 * Every place a kind can run (D5): `primary` ones first (listed first in
 * `Change`), the rest under `More places to run`; a mode this entry can't
 * use carries `disabledReason`.
 * @param {string} kind  run kind (`code`, `harness`, `http`, …)
 * @param {string} [entryId]
 * @returns {{id, label, status, primary, disabledReason?}[]}
 */
export function modesFor(kind, entryId) {
  const entry = catalogEntry(entryId);
  const kindModes = KIND_MODES[kind] ?? [];
  const own = entry?.modes ?? kindModes;
  const primary = entry?.primaryModes ?? kindModes.slice(0, 1);
  const rows = kindModes.map((id) => {
    const usable = own.includes(id);
    const unavailable = !usable;
    const reason = unavailable
      ? UNAVAILABLE[kind]?.[id]?.(entry) ??
        `${entry?.name ?? "This source"} can't run here.`
      : null;
    return {
      id,
      label: modeLabel(id, entry),
      status: modeStatus(kind, id, entry),
      primary: primary.includes(id) && !unavailable,
      ...(reason ? { disabledReason: reason } : {}),
    };
  });
  return [...rows.filter((m) => m.primary), ...rows.filter((m) => !m.primary)];
}

const norm = (s) =>
  String(s ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

// The separator-free form (`gpt-4o-mini` → `gpt4omini`).
const squash = (s) => norm(s).replace(/ /g, "");

/**
 * L7c g2 (picker:search): `text => boolean` for a query, matching the
 * spaced form (`gpt 4o`) or the separator-free one (`gpt4o`); null for an
 * empty query (pure).
 */
export function matcherOf(q) {
  const query = norm(q);
  if (!query) return null;
  const tight = squash(q);
  return (text) => {
    if (!text) return false;
    return norm(text).includes(query) || squash(text).includes(tight);
  };
}

/**
 * Search entries by name, short name, aliases, languages, package or group
 * (`HTTP`, `a2a`, `mcp`, `traces`). Prefix matches rank first. With
 * `availability`, only the entries the server can run (for offers such as
 * notRunHere); the picker searches without it and greys refused tiles.
 * @param {string} q
 * @param {object} [availability]  runSourceAvailability.js
 */
export function searchCatalog(q, availability) {
  // L6: no Already running entries where the launch hides them.
  if (!runningShown()) return [];
  const entries = runnable(ALL, availability);
  const query = norm(q);
  if (!query) return entries;
  const has = matcherOf(q);
  const scored = entries
    .map((e) => {
      const name = norm(e.name);
      const words = [
        e.name,
        e.short,
        ...(e.aliases ?? []),
        ...(e.languages ?? []),
        e.instrument?.pip,
        e.instrument?.npm,
        GROUP_LABELS[e.group],
        BADGES[e.badge],
      ];
      let score = 0;
      if (name === query) score = 4;
      else if (name.startsWith(query)) score = 3;
      else if (norm(e.short).startsWith(query)) score = 3;
      else if (words.some(has)) score = 1;
      return { e, score };
    })
    .filter((x) => x.score > 0);
  return scored.sort((a, b) => b.score - a.score).map((x) => x.e);
}

/**
 * The held entries a step-1 search names (`traces`, `upload`), drawn
 * greyed after searchCatalog's results; never offered (pure).
 * @param {string} q
 */
export function searchHeldEntries(q) {
  const has = matcherOf(q);
  if (!has) return [];
  return heldEntriesFor("running").filter((e) =>
    [e.name, e.short, ...(e.aliases ?? [])].some(has),
  );
}

/**
 * Graph quality of a run from its config (D12): `full` for Agent graph runs
 * and for code whose `framework.graph_source` is `connect_sdk` or `manual`;
 * else the catalog entry's quality (`parent_child`, `model_calls`,
 * `unknown`). Prompt runs are model + tool spans (`parent_child`).
 * @param {object} runConfig  run.config
 * @returns {"full"|"parent_child"|"model_calls"|"unknown"}
 */
export function graphQualityOf(runConfig) {
  const c = runConfig ?? {};
  if (c.kind === "agent") return "full";
  const source = c.framework?.graph_source;
  if (source === "connect_sdk" || source === "manual") return "full";
  if (c.kind === "prompt") return "parent_child";
  const entry = entryOfConfig(c);
  return entry?.graph?.quality ?? "unknown";
}

/**
 * The catalog entry a run config points at: `framework.id` (code),
 * `harness.id` (harness), `endpoint` (http), the
 * kind itself for traces / upload.
 * @param {object} runConfig
 */
export function entryOfConfig(runConfig) {
  const c = runConfig ?? {};
  if (c.framework?.id) return catalogEntry(c.framework.id);
  if (c.kind === "harness") {
    const id =
      c.harness?.id ??
      HARNESSES.find((h) => h.name === c.harness?.name)?.id ??
      null;
    return catalogEntry(id);
  }
  if (c.kind === "http") return catalogEntry("http");
  if (c.kind === "a2a" || c.kind === "mcp_tool") return catalogEntry(c.kind);
  if (c.kind === "traces" || c.kind === "upload") return catalogEntry(c.kind);
  if (c.kind === "code") {
    const fw = String(c.code?.framework ?? "").toLowerCase();
    return FRAMEWORKS.find((f) => fw.startsWith(f.name.toLowerCase())) ?? null;
  }
  return null;
}

/**
 * Kept for callers (options-truth L1 C11): no entry is Preview or Coming
 * soon any more, so there is never a status reason. The OTel badge carries
 * its fact through `BADGES` / `OTEL_PREVIEW_REASON` instead.
 */
export function previewReason() {
  return null;
}
