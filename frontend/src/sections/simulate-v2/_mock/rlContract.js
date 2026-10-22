/**
 * The RL environment contract — the keystone.
 *
 * Everything upstream (reading the agent) and everything downstream (running,
 * scoring, training) meets here. The contract is what makes an environment an
 * *environment* rather than a test script: an adapter that fixes the modality,
 * an observation and action space, transition dynamics, a reward spec, and an
 * episode contract that says when a run is over.
 *
 * Two properties worth preserving:
 *
 *   Generic core, adapter-filled.  Observation and action are the same five
 *   fields in every modality. The adapter says what fills them. That is why a
 *   voice environment and a coding environment can share one runner.
 *
 *   Reward is not invented here.  The verifiers ARE the evals that grade a
 *   run, given weights. If reward had its own definition you could train an
 *   agent that scores well and still fails the release gate.
 */
import { MODALITY_FOR } from "./fidelity";
import { getAgentType } from "./agentTypes";
import { actorsOf } from "./actors";
import { toolImplFor } from "./envInternals";
import { argSpecOf } from "./toolArgSpec";
import { contractFor } from "./contract";
import { legacyUseCaseId, rulesOf, ruleOfScenario, subGoalsOfScenario, useCaseId as stableCaseId } from "./envModel";
import { currentEnvVersion, environmentVersions, envLineage, holdsInEnv, nextEnvVersion, pendingEnvChanges, WORLD_CONFIG_KEYS } from "./versions";
import { currentEnvSnapshot, envSnapshotsOf, nextSnapshotLabel, SAME_ACTION_MS } from "./envSnapshot";
import { callerChangePatch, simulatorOf } from "./envModel";
import { suiteLogPatch, largerBump } from "./suiteLog";
import { bumpScenario } from "./decoupled";
import { archivedOf, archiveScenario, restoreScenario } from "./scenarioStates";
import { nextCatalogVersion, subGoalVersionOf } from "./subGoalCatalog";
import { envTurnLimitOf } from "./runStream";
import { resolveEval } from "./evals";
import { envLabelOf } from "./envVersionKinds";
import { proofStatus, provedAgainst } from "./proofs";
import { ruleChangesOnFlow } from "./scenarioStates";
import { knownToolsFor, requiredToolsOf, withPinnedWorld } from "./toolFit";

/* ── 1. modality adapter ─────────────────────────────────────────────────── */

export const ADAPTERS = [
  {
    id: "chat", label: "Chat", icon: "solar:chat-round-line-linear", color: "#2563EB",
    blurb: "Turn-taking text. The counterpart types.",
    observation: { transcript: "list[turn]", ui_state: "null" },
    action: ["say", "call_tool", "finish"],
  },
  {
    id: "voice", label: "Voice", icon: "solar:phone-calling-linear", color: "#7857FC",
    blurb: "Duplex audio. Interruptions and dead air are part of the state.",
    observation: { transcript: "list[turn]", audio_state: "{noise, barge_in, latency_ms}" },
    action: ["say", "call_tool", "finish"],
  },
  {
    id: "cua", label: "Computer use", icon: "solar:monitor-linear", color: "#0D9488",
    blurb: "A screen the agent looks at and clicks.",
    observation: { screenshot: "image", dom: "tree" },
    action: ["click", "type", "scroll", "call_tool", "finish"],
  },
  {
    id: "coding", label: "Coding", icon: "solar:code-square-linear", color: "#EA580C",
    blurb: "A repo, a test suite and a shell.",
    observation: { repo_diff: "patch", test_report: "junit" },
    action: ["edit_file", "run_tests", "call_tool", "finish"],
  },
  {
    id: "physical", label: "Physical", icon: "solar:cpu-bolt-linear", color: "#DC2626",
    blurb: "Robotics and worldsims. Continuous control, real clock.",
    observation: { sensors: "dict[str, float]", frame: "image", pose: "se3" },
    action: ["actuate", "grasp", "move_to", "finish"],
    future: true,
  },
  {
    id: "custom", label: "Custom", icon: "solar:widget-4-linear", color: "#CA8A04",
    blurb: "Bring your own spaces. The core five fields still apply.",
    observation: { payload: "your schema" },
    action: ["your verbs"],
    custom: true,
  },
];

/**
 * The modality actually in force.
 *
 * Not a setting. The modality is a consequence of what the agent *is*: you
 * chose the agent type when you connected, and the observation space, the
 * action space, the fidelity controls and the runtime connection all fall out
 * of it. A free-standing override on the contract page re-opened that settled
 * question and — worse — only reached two of those four, so a coding adapter
 * could sit on a phone line while the scenarios stayed voice.
 *
 * So there is one resolution point, and it reads the connected agent. An agent
 * that fits the environment's surface leaves the modality where it was; an
 * agent from a different surface moves it, and everything derived moves with
 * it. Correcting a misread means reconnecting on the Agent page.
 */
export const effectiveModality = (env, envState) => {
  const type = getAgentType(envState?.agent?.typeId);
  if (type && !type.surfaces.includes(env?.surface)) {
    return MODALITY_FOR[type.surfaces[0]] || "chat";
  }
  return MODALITY_FOR[env?.surface] || "chat";
};

/** The adapter in force, resolved the same single way. */
export const adapterOf = (env, envState) =>
  ADAPTERS.find((a) => a.id === effectiveModality(env, envState)) || ADAPTERS[0];

/**
 * True when the connected agent moved the modality off the environment's own
 * surface — the one case where the contract is not what the template promised.
 */
export const modalityMovedBy = (env, envState) => {
  const type = getAgentType(envState?.agent?.typeId);
  if (!type || type.surfaces.includes(env?.surface)) return null;
  return type;
};

/** The environment as its settings currently stand, for anything deriving from them. */
export const effectiveEnv = (env, envState) => ({
  ...env,
  difficulty: envState?.difficulty || env?.difficulty,
});

export const adapterFor = (env) =>
  ADAPTERS.find((a) => a.id === (MODALITY_FOR[env?.surface] || "chat")) || ADAPTERS[0];

export const getAdapter = (id) => ADAPTERS.find((a) => a.id === id);

/* ── 2. observation + action ─────────────────────────────────────────────── */

/**
 * The generic core is fixed. The adapter fills it. Showing both columns is the
 * point — it is what makes "one runner, six modalities" a claim you can check
 * rather than a slogan.
 */
export const observationSpace = (env, adapter) => {
  const a = adapter || adapterFor(env);
  return [
    { field: "goal", type: "str", filled: "The scenario's task, as the persona states it.", generic: true },
    { field: "world", type: "dict", filled: `${env?.seed?.tables?.length || 0} seeded tables, reset each episode.`, generic: true },
    { field: "history", type: "list[step]", filled: "Every action and result so far this episode.", generic: true },
    { field: "sub_goals", type: "list[check]", filled: "The checks not yet settled.", generic: true },
    ...Object.entries(a.observation).map(([field, type]) => ({
      field, type, filled: `Filled by the ${a.label.toLowerCase()} adapter.`, generic: false,
    })),
  ];
};

export const actionSpace = (env, adapter) => {
  const a = adapter || adapterFor(env);
  const tools = env?.tools || [];
  return a.action.map((verb) => ({
    verb,
    args:
      verb === "call_tool" ? `name: enum[${tools.length} tools], args: dict`
        : verb === "say" ? "text: str"
          : verb === "finish" ? "summary: str"
            : "adapter-defined",
    note:
      verb === "call_tool" ? "The tools on the contract, with their real argument names."
        : verb === "finish" ? "Ends the episode. Terminal — see the episode contract."
          : `Provided by the ${a.label.toLowerCase()} adapter.`,
  }));
};

/* ── 3. transition dynamics ──────────────────────────────────────────────── */

/**
 * What moves the world between steps, and it is never only the agent: the
 * personas act, the mocked integrations answer, and the perturbations happen
 * whether anyone asked for them or not.
 */
export const transitionDynamics = (env, envState) => {
  const tools = env?.tools || [];
  const stubbed = tools.filter((t) => /refund|issue|charge|send|delete|book/i.test(t.name));
  return [
    {
      id: "actors",
      label: "Actors",
      icon: "solar:users-group-two-rounded-linear",
      value: `${actorsOf(env, envState).length} in this environment`,
      note: "Other parties with goals of their own, pulling against the task — a colleague arguing for something else, a supervisor with their own criteria. They are dynamics, not task: the persona states the goal, the actor pulls against it.",
      to: "actors",
    },
    (() => {
      /*
        Integrations show as one of two states:
          · If twins are attached, this is a real stateful sandbox and
            the row reflects that — scenarios can test the end state,
            not just the decision.
          · If no twins are attached, we fall back to shallow replay
            (the historical behaviour) and surface an "upgrade" hint
            that points to the Twins tab.
      */
      /* Twin state lives on envState.twinBacking.services — `envState.twins`
         was never written anywhere, so this always fell through to "Mocked
         integrations" and told a twin-backed env to go attach the twins it
         already has (while Overview showed the sandbox serving live). */
      const twins = envState?.twinBacking?.services || [];
      if (twins.length > 0) {
        return {
          id: "integrations",
          label: "Twinned services",
          icon: "solar:server-square-linear",
          value: `${twins.length} twin${twins.length === 1 ? "" : "s"}`,
          note: "Third-party services the agent talks to are running as stateful twins seeded per-scenario. Runs test what actually landed — a message in Slack, a row in Salesforce — not just the decision to call.",
          to: "twins",
        };
      }
      return {
        id: "integrations",
        label: "Mocked integrations",
        icon: "solar:plug-circle-linear",
        value: `${stubbed.length || 1} stubbed`,
        note: `${stubbed.length ? stubbed.map((t) => t.name).join(", ") : "One write tool"} reaches outside the sandbox, so it replays a recorded response instead of firing. Scenarios ending there test the decision, not the delivery. Attach twins to test what actually landed.`,
        to: "twins",
      };
    })(),
    {
      id: "perturbations",
      label: "Perturbations",
      icon: "solar:tuning-2-linear",
      value: "per modality",
      note: "Noise, barge-in, typos, latency, flaky downstreams. Applied by the adapter so the same scenario is a different problem at a different fidelity.",
      to: "fidelity",
    },
  ];
};

/* ── 4. reward spec ──────────────────────────────────────────────────────── */

export const VERIFIER_KINDS = [
  { id: "goal", label: "Goal", color: "#16A34A", blurb: "Did the task get done. Terminal, sparse." },
  { id: "rubric", label: "Rubric", color: "#2563EB", blurb: "How well, judged per turn. Dense." },
  { id: "constraint", label: "Constraints", color: "#DC2626", blurb: "What must never happen. Penalty." },
];

export const rewardSpec = (env) => {
  const rules = env?.rules || [];
  return {
    goal: [
      { name: "task_completed", weight: 1.0, note: "The scenario's stated outcome was reached." },
      { name: "under_reference_length", weight: 0.2, note: "Done in no more steps than the reference solution." },
    ],
    rubric: [
      { name: "sub_goal_settled", weight: 0.15, note: "Each check that settles pays out when it settles." },
      { name: "grounded", weight: 0.1, note: "Claims trace to a tool result or seeded row." },
    ],
    constraint: [
      ...rules.slice(0, 3).map((r) => ({ name: "hard_rule_violation", weight: -1.0, note: r })),
      { name: "step_cost", weight: -0.01, note: "Every step, so dithering is not free." },
    ],
  };
};

/* ── 5. episode contract ─────────────────────────────────────────────────── */

/**
 * Terminate and truncate are different endings and conflating them corrupts
 * training: a truncated episode has no terminal value and must be bootstrapped,
 * while a terminated one does. Stating both here is what lets the RL interface
 * return an honest `done`.
 */
export const episodeContract = (env, envState) => {
  const adapter = adapterOf(env, envState);
  const realTime = adapter.id === "voice" || adapter.id === "physical";
  /* The turn limit Settings sets — the one a run actually stops at. */
  const turns = envTurnLimitOf(envState);
  return {
    terminate: [
      { when: "The agent says it's done", note: "The checks decide whether it passed, from where the conversation ended up." },
      { when: "A hard rule is broken", note: "It ends there and fails — nothing after it could make up for it." },
      { when: "The caller hangs up or leaves", note: "It's graded on what was settled by then." },
    ],
    truncate: [
      { when: `${turns} agent turns`, note: "It stops and fails with “Hit the turn limit”. Change the limit in Settings, under Simulation defaults." },
      { when: realTime ? "8 minutes" : "12 minutes", note: "It stops and the sandbox is cleared. This one isn't counted as a failure." },
    ],
    clock: {
      mode: realTime ? "Real time" : "Turn by turn",
      note: realTime
        ? "Pauses and slow replies are part of the test, so time runs as it would on a real call."
        : "The world only moves when the agent acts, so a slow model isn't marked down for being slow.",
    },
    seed: {
      note: "Every conversation starts from the same data. Run it again and the world, the caller's turns and the tool answers come back the same — so a failing scenario can be replayed and debugged.",
    },
  };
};

/* ── completeness ────────────────────────────────────────────────────────── */

export const contractParts = (env, envState) => [
  { id: "adapter", label: "Modality adapter", done: true },
  { id: "spaces", label: "Observation + action", done: true },
  /* Always derived from the env by the adapter (like adapter/spaces/episode
     above). The old `(personas).length > 0 || true` was dead — `|| true` forced
     it true anyway, and `envState.personas` is never populated (actors live on
     envState.actors). */
  { id: "dynamics", label: "Transition dynamics", done: true },
  { id: "reward", label: "Reward spec", done: (envState?.evals || []).length > 0 },
  { id: "episode", label: "Episode contract", done: true },
];

/* ── tools: arguments and their allowed values (the contract's spec) ─────── */

/*
  Each tool's arguments, with their type and what values the contract allows
  — enums, ranges, required flags. Read from the agent's code at build time
  ("Taking each tool's signature from the code rather than its name, so the
  arguments and their permitted values are exact"); the mock keeps the known
  signatures here and derives a plain one for any other tool. Corrections
  made after the build (`envState.toolArgEdits`, keyed "tool.arg") lay over
  them — each one minted an environment version.

  An argument: { name, type: str|int|float|bool|enum|date, required,
  values?: [..] (enum, or a bool that must be one value), min?, max?
  (number, or a word like "order total"), unit?, format?, note? }
*/
/*
  Where the names come from, in order: the tool's own signature as the build
  read it (`tool.args`, e.g. issue_refund(order_id, amount, caller_confirmed)),
  else the handler the World tab shows (`toolImplFor`), so the two never
  disagree. What each argument allows comes from `toolArgSpec.js` — by tool
  and argument where it differs, else by argument name.
*/
/* "order_id: str, email: str | None = None" → [{ name, pyType, optional }]. */
const parseSignature = (sig) => String(sig || "").split(/,(?![^[]*\])/).map((x) => x.trim()).filter((x) => x && !x.startsWith("*")).map((x) => {
  const [name, rest = ""] = x.split(":").map((y) => y.trim());
  return { name, pyType: rest.split("=")[0].replace(/\| None/, "").trim(), optional: /=/.test(rest) };
});
const PY_TYPE = { str: "str", int: "int", float: "float", bool: "bool", dict: "object", "list[str]": "enum" };

const specFor = (tool, name, pyType) => ({ type: PY_TYPE[pyType] || "str", ...argSpecOf(tool, name) });

/* Any other tool: an id named for what it acts on ("cancel_booking" → booking_id). */
const derivedArgs = (tool) => {
  const parts = String(tool?.name || "").split("_");
  const noun = parts.length > 1 ? parts.slice(1).join("_") : null;
  return noun ? [{ name: `${noun}_id`, type: "str", required: true }] : [];
};

const baseArgsOf = (tool) => {
  if (Array.isArray(tool?.args)) {
    return tool.args.map((name) => ({ name, required: true, ...specFor(tool.name, name, null) }));
  }
  const sig = toolImplFor(tool).code.match(/def [a-z0-9_]+\((.*?)\) ->/)?.[1] || "";
  if (sig.includes("**")) return derivedArgs(tool);
  return parseSignature(sig).map((a) => ({ name: a.name, required: !a.optional, ...specFor(tool.name, a.name, a.pyType) }));
};

const argKey = (tool, arg) => `${tool}.${arg}`;

/** One tool's arguments, corrections applied. */
export const toolArgsOf = (tool, envState) => {
  const base = baseArgsOf(tool);
  const edits = envState?.toolArgEdits || {};
  const lineage = envLineage(null, envState);
  return base.map((a) => {
    const e = edits[argKey(tool.name, a.name)];
    if (!e || !holdsInEnv(lineage, e.envVersion)) return a;
    const { at, envVersion, from, ...change } = e;
    return { ...a, ...change, editedIn: envVersion || null };
  });
};

/**
 * The contract's exact words that changed in one world version — what the
 * version history shows under it, so "1 rule added" comes with the rule:
 *   [{ what: "Rule R7", kind: "added" | "changed" | "removed", from, to }]
 * Rules and use cases read as they stood before and after; an argument
 * reads as the values it allows.
 */
export const contractWordingIn = (env, envState, label) => {
  if (!label) return [];
  const out = [];
  /* Walk a change log in order, keeping each id's wording, and note the
     changes that landed in `label` against the wording before them. */
  const walk = (log, base, name) => {
    const words = new Map(base);
    (log || []).forEach((c) => {
      if (c.removal) return; // the re-check twin of a removal, not a change of words
      const before = words.get(c.id) ?? null;
      if (c.envVersion === label) {
        out.push({ what: `${name} ${c.id}`, kind: c.kind, from: c.kind === "added" ? null : before, to: c.kind === "removed" ? null : c.to });
      }
      if (c.kind === "removed") words.delete(c.id);
      else words.set(c.id, c.to);
    });
  };
  walk(envState?.ruleChanges, (env?.rules || []).map((text, i) => [`R${i + 1}`, text]), "Rule");
  walk(envState?.useCaseChanges, contractFor(env).useCases.map((text) => [stableCaseId(text), text]), "Use case");
  Object.entries(envState?.toolArgEdits || {}).forEach(([key, e]) => {
    if (e?.envVersion !== label) return;
    const [toolName, argName] = key.split(".");
    const tool = (env?.tools || []).find((t) => t.name === toolName);
    const arg = tool ? baseArgsOf(tool).find((a) => a.name === argName) : null;
    const { at, envVersion, from, ...change } = e;
    out.push({ what: key, kind: "changed", from: from || null, to: allowedLine({ ...(arg || {}), ...change }) });
  });
  return out;
};

/** "order_id · email · phone", "0–order total £", "true only", "A-#####" — what the contract allows. */
export const allowedLine = (a) => {
  if (!a) return "";
  if (a.type === "enum") return (a.values || []).join(" · ");
  if (a.type === "bool") return a.values?.length === 1 ? `${a.values[0]} only` : "true · false";
  if (a.min != null || a.max != null) {
    const words = typeof a.min !== "number" || typeof a.max !== "number";
    const unit = a.unit && a.unit !== "£" ? (words ? `, in ${a.unit}` : ` ${a.unit}`) : "";
    const pre = a.unit === "£" ? "£" : "";
    const lo = a.min != null ? `${typeof a.min === "number" ? pre : ""}${a.min}` : "any";
    const hi = a.max != null ? `${typeof a.max === "number" ? pre : ""}${a.max}` : "any";
    return a.min === a.max ? `${lo} only${unit}` : `${lo}–${hi}${unit}`;
  }
  return a.format || "any";
};

/** "order_id: str (A-#####) · amount: float (£0–order total)" — one tool's signature, as the build narrates it. */
export const argsSignatureLine = (tool, envState) => toolArgsOf(tool, envState)
  .map((a) => {
    const allowed = allowedLine(a);
    const extra = [allowed !== "any" ? allowed : null, a.note].filter(Boolean).join(" — ");
    return `${a.name}: ${a.type}${extra ? ` (${extra})` : ""}`;
  })
  .join(" · ");

/** An argument that rules values out — a list, a fixed yes/no, or a range. Plain formats (A-#####) aren't limits. */
export const isArgLimit = (a) => a.type === "enum" || (a.type === "bool" && a.values?.length === 1) || a.min != null || a.max != null;

/** Every tool on the contract with its arguments — what the Contract tab lists. */
export const contractToolsOf = (env, envState) => (env?.tools || []).map((t) => ({ ...t, args: toolArgsOf(t, envState) }));

/* ── use cases, with corrections ─────────────────────────────────────────── */

/**
 * The use cases the contract lists, each with an id that survives
 * rewording — corrections (`envState.useCaseChanges`) keep the id and
 * record the new words, the way rule changes do.
 */
export const contractUseCasesOf = (env, envState) => {
  const lineage = envLineage(env, envState);
  const changes = (envState?.useCaseChanges || []).filter((c) => holdsInEnv(lineage, c.envVersion));
  const removed = new Set(changes.filter((c) => c.kind === "removed").map((c) => c.id));
  /* Corrections saved under the older, shorter id still apply — where that id
     named only one use case. */
  const legacyCount = new Map();
  contractFor(env).useCases.forEach((t) => legacyCount.set(legacyUseCaseId(t), (legacyCount.get(legacyUseCaseId(t)) || 0) + 1));
  const base = contractFor(env).useCases.map((text) => {
    const id = stableCaseId(text);
    const legacy = legacyCount.get(legacyUseCaseId(text)) === 1 ? legacyUseCaseId(text) : null;
    const last = [...changes].reverse().find((c) => (c.id === id || (legacy && c.id === legacy)) && c.kind === "changed");
    return { id, text: last?.to || text, original: text, changedIn: last?.envVersion || null };
  });
  const added = changes.filter((c) => c.kind === "added").map((c) => ({ id: c.id, text: c.to, original: null, changedIn: c.envVersion }));
  return [...base, ...added].filter((u) => !removed.has(u.id));
};

/* ── correcting the contract after the build (v7) ────────────────────────── */

/*
  A correction to the contract — a rule, a use case, or the values a tool
  argument allows — is a change to this environment's spec. Each one states
  what it costs before it lands ("This creates environment v4; 6 scenarios
  that use issue_refund will be marked Re-check"), then mints the next
  environment version and marks the scenarios that depend on it Re-check.
  Everything else carries over to the new version unchanged.

    edit = { target: "rule" | "useCase" | "toolArg", op: "add" | "edit" | "remove",
             id?, text?, tool?, arg?, values?, add?, remove?, min?, max? }

  The chat (chatActions.js) and the Contract tab's Edit both go through
  `contractEditPlan` → `plan.patch(latestEnvState)`.
*/
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const clip = (s, n = 64) => (String(s || "").length > n ? `${String(s).slice(0, n - 1)}…` : String(s || ""));

const rowsOf = (envState) => (envState?.scenarios || []).filter((r) => !r.retired && !r.archived);

/* The scenarios a rule touches — the ones testing it, and the ones whose
   flow calls a tool it names — exactly as the Scenarios tab will read them. */
const ruleDependents = (env, envState, change) => {
  const known = knownToolsFor(env, envState);
  const probe = { ...envState, ruleChanges: [change] };
  const rules = rulesOf(env, envState);
  return rowsOf(envState).filter((row) => ruleOfScenario(row, rules)?.id === change.id
    || ruleChangesOnFlow(row, { env, envState: probe, tools: requiredToolsOf(row, known) }).length > 0).map((r) => r.id);
};

const toolDependents = (env, envState, tool) => {
  const known = knownToolsFor(env, envState);
  return rowsOf(envState).filter((row) => requiredToolsOf(row, known).includes(tool)
    || subGoalsOfScenario(row, env, envState).some((g) => g.id === `SG-tool-${tool}`)).map((r) => r.id);
};

const caseDependents = (env, envState, uc) => {
  const tool = String(uc.original || uc.text).match(/using ([a-z0-9_]+)\.?$/i)?.[1];
  const byTool = tool ? new Set(toolDependents(env, envState, tool)) : new Set();
  return rowsOf(envState).filter((row) => (row.useCase && (stableCaseId(row.useCase) === uc.id || row.useCase === uc.text || row.useCase === uc.original))
    || byTool.has(row.id)).map((r) => r.id);
};

const nextRuleId = (env, envState) => {
  const ids = [...(env?.rules || []).map((_, i) => i + 1), ...(envState?.ruleChanges || []).map((c) => parseInt(String(c.id).replace(/\D/g, ""), 10) || 0)];
  return `R${Math.max(0, ...ids) + 1}`;
};

const sameList = (a = [], b = []) => a.length === b.length && a.every((x, i) => x === b[i]);

/**
 * What a correction would do, before it lands: { ok, error?, title, subject,
 * next, affected[], costLine, detail, patch(latestEnvState) }.
 */
export const contractEditPlan = (env, envState, edit) => {
  const world = withPinnedWorld(env, envState) || env;
  const fail = (error) => ({ ok: false, error });
  /* Named as the environment version it mints (a snapshot — envSnapshot.js);
     the world version underneath is minted in `patch`. */
  const next = nextSnapshotLabel(env, envState);
  let kind;
  let detail;
  let subject;
  let affected = [];
  let title;
  let apply;

  if (edit.target === "rule") {
    const rules = rulesOf(env, envState);
    const rule = edit.op === "add" ? null : rules.find((r) => r.id.toLowerCase() === String(edit.id || "").toLowerCase());
    if (edit.op !== "add" && !rule) return fail(`There's no rule ${edit.id || ""} on this contract. Rules are ${rules.map((r) => r.id).join(", ") || "none yet"}.`);
    const text = String(edit.text || "").trim().replace(/^["“]|["”]$/g, "");
    if (edit.op !== "remove" && !text) return fail("Say what the rule should read.");
    if (edit.op === "edit" && text === rule.text) return fail(`${rule.id} already reads that way — nothing to change.`);
    kind = "rules";
    const id = rule?.id || nextRuleId(env, envState);
    const probe = edit.op === "add"
      ? { id, kind: "added", to: text }
      : { id, kind: "changed", to: edit.op === "remove" ? rule.text : text };
    affected = ruleDependents(env, envState, probe);
    subject = rule ? `that test or walk through rule ${id}` : "that the new rule touches";
    title = edit.op === "add" ? `Add rule ${id}` : edit.op === "remove" ? `Remove rule ${id}` : `Change rule ${id}`;
    detail = edit.op === "add" ? `Rule ${id} added: ${clip(text)}` : edit.op === "remove" ? `Rule ${id} removed: ${clip(rule.text)}` : `Rule ${id} changed: ${clip(text)}`;
    apply = (es, label, now) => {
      const entries = edit.op === "remove"
        ? [{ id, kind: "removed", envVersion: label, at: now, scenarios: affected },
          /* Re-check the scenarios that tested it or walked through it. */
          { id, kind: "changed", to: rule.text, envVersion: label, at: now, scenarios: affected, removal: true }]
        : [{ ...probe, envVersion: label, at: now, scenarios: affected }];
      return { ruleChanges: [...(es?.ruleChanges || []), ...entries] };
    };
  } else if (edit.target === "useCase") {
    const list = contractUseCasesOf(env, envState);
    const uc = edit.op === "add" ? null : list.find((u) => u.id.toLowerCase() === String(edit.id || "").toLowerCase());
    if (edit.op !== "add" && !uc) return fail(`There's no use case ${edit.id || ""} on this contract.`);
    const text = String(edit.text || "").trim().replace(/^["“]|["”]$/g, "");
    if (edit.op !== "remove" && !text) return fail("Say what the use case should read.");
    kind = "contract";
    const id = uc?.id || stableCaseId(text);
    if (edit.op === "add" && list.some((u) => u.id === id)) return fail(`That use case is already on the contract (${id}).`);
    affected = uc ? caseDependents(env, envState, uc) : [];
    subject = uc ? `in use case ${id}` : "in the new use case";
    title = edit.op === "add" ? "Add a use case" : edit.op === "remove" ? `Remove use case ${id}` : `Change use case ${id}`;
    detail = edit.op === "add" ? `Use case ${id} added: ${clip(text)}` : edit.op === "remove" ? `Use case ${id} removed: ${clip(uc.text)}` : `Use case ${id} reworded: ${clip(text)}`;
    apply = (es, label, now) => ({
      useCaseChanges: [...(es?.useCaseChanges || []), {
        id, kind: { add: "added", edit: "changed", remove: "removed" }[edit.op], ...(edit.op !== "remove" && { to: text }), envVersion: label, at: now, scenarios: affected,
      }],
    });
  } else if (edit.target === "toolArg") {
    const tool = (world?.tools || []).find((t) => t.name === edit.tool);
    if (!tool) return fail(`There's no tool ${edit.tool || ""} on this contract.`);
    const args = toolArgsOf(tool, envState);
    const arg = args.find((a) => a.name === edit.arg) || (args.length === 1 ? args[0] : null);
    if (!arg) return fail(`${tool.name} takes ${args.map((a) => a.name).join(", ") || "no arguments"} — say which one.`);
    let change = null;
    if (arg.type === "enum" || (arg.type === "bool" && edit.values)) {
      const cur = arg.values || [];
      const values = edit.values
        ? edit.values
        : edit.add ? [...new Set([...cur, ...edit.add])]
          : edit.remove ? cur.filter((v) => !edit.remove.includes(v)) : cur;
      if (!values.length) return fail(`${tool.name}.${arg.name} has to allow at least one value.`);
      if (edit.remove && edit.remove.every((v) => !cur.includes(v))) return fail(`${tool.name}.${arg.name} doesn't allow ${edit.remove.join(", ")} now — it allows ${cur.join(", ")}.`);
      if (sameList(values, cur)) return fail(`${tool.name}.${arg.name} already allows exactly that.`);
      change = { type: arg.type === "bool" ? "bool" : "enum", values };
    } else if (edit.min != null || edit.max != null) {
      const min = edit.min != null ? edit.min : arg.min;
      const max = edit.max != null ? edit.max : arg.max;
      if (typeof min === "number" && typeof max === "number" && min > max) return fail(`The lowest value (${min}) is above the highest (${max}).`);
      if (min === arg.min && max === arg.max) return fail(`${tool.name}.${arg.name} already allows ${allowedLine(arg)}.`);
      change = { min, max };
    } else if (edit.values) {
      change = { type: "enum", values: edit.values };
    } else {
      return fail(`Say what ${tool.name}.${arg.name} should allow — a list of values, or a range like 0 to 500.`);
    }
    kind = "contract";
    affected = toolDependents(env, envState, tool.name);
    subject = `that use ${tool.name}`;
    const after = { ...arg, ...change };
    title = `${tool.name}.${arg.name}: ${allowedLine(arg)} → ${allowedLine(after)}`;
    detail = `${tool.name}.${arg.name} allows ${allowedLine(after)} (was ${allowedLine(arg)})`;
    apply = (es, label, now) => ({
      toolArgEdits: { ...(es?.toolArgEdits || {}), [argKey(tool.name, arg.name)]: { ...change, from: allowedLine(arg), envVersion: label, at: now } },
    });
  } else {
    return fail("That isn't a part of the contract I can change.");
  }

  const n = affected.length;
  /* One draft per environment: edits still unsaved ride along, said up front. */
  const waiting = pendingEnvChanges(envState).length;
  const alsoSaves = waiting ? ` Your ${plural(waiting, "unsaved edit")} ${waiting === 1 ? "is" : "are"} saved with it.` : "";
  const costLine = (n
    ? `This creates environment ${next}; ${plural(n, "scenario")} ${subject} will be marked Re-check.`
    : `This creates environment ${next}; there are no scenarios ${subject}, so none is marked Re-check.`) + alsoSaves;

  /* Against the newest state: mint the version (folding in any edits still
     pending, as saving them would), record the correction, and carry every
     scenario that doesn't depend on it over to the new version. */
  const patch = (latest = envState) => {
    const now = new Date().toISOString();
    const list = latest?.envVersions?.length ? latest.envVersions : [...environmentVersions(env, latest)].reverse();
    /* Edits still pending are folded in by nextEnvVersion. */
    const minted = nextEnvVersion(env, latest, { changed: [kind], note: detail, now });
    const hit = new Set(affected);
    const scenarios = (latest?.scenarios || []).map((row) => {
      if (hit.has(row.id) || row.retired || row.archived) return row;
      if (proofStatus(row, env, latest).stale) return row;
      const on = row.provedOn || [provedAgainst(row, env, latest)];
      return { ...row, provedOn: [...new Set([...on.filter(Boolean), minted.label])] };
    });
    return {
      envVersions: [...list, minted],
      activeEnvVersion: minted.label,
      unsavedEnvChanges: [],
      scenarios,
      ...apply(latest, minted.label, now),
    };
  };

  return { ok: true, title, subject, next, affected, costLine, detail, kind, patch };
};

/* ── rolling back ─────────────────────────────────────────────────────────── */

/*
  Roll back = a new environment version whose world and contract are an
  older one's, the way a revert works: nothing is deleted, numbering only
  moves forward, and every run still names exactly what it ran on. Its
  parent is the version it rolls back to, so the rules, use cases and
  argument values changed in between stop holding (envLineage); the tools
  and the world's settings (tool answers and faults, actors, seed) are the
  older version's, and a scenario that needs a tool the older world lacks
  sits out until the agent and world agree again. This is the world part of
  a rollback; snapshotRollbackPlan (below) restores the rest of the version
  — scenarios, sub-goals, evaluations and caller — in the same new version.

  { ok, target, next, changes: [lines], discards, recheck, patch(latest) }
*/
const sameJson = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export const rollbackPlan = (env, envState, targetLabel, { displayName } = {}) => {
  const list = environmentVersions(env, envState);
  const target = list.find((v) => v.label === targetLabel);
  const current = list.find((v) => v.current) || list[0];
  if (!target || !current || target.label === current.label) return { ok: false, error: "That's the version in use." };
  const then = { ...envState, activeEnvVersion: target.label };
  const name = (v) => (displayName && v === target ? displayName : `environment ${envLabelOf(env, envState, v.label)}`);

  /* What actually moves, said before it does. */
  const toolNames = (v) => new Set((v.tools || env?.tools || []).map((t) => t.name));
  const toolsNow = toolNames(current);
  const toolsThen = toolNames(target);
  const gone = [...toolsNow].filter((t) => !toolsThen.has(t));
  const back = [...toolsThen].filter((t) => !toolsNow.has(t));
  const rulesNow = rulesOf(env, envState);
  const rulesThen = rulesOf(env, then);
  const ruleMoves = [...new Set([...rulesNow, ...rulesThen].map((r) => r.id))]
    .filter((id) => rulesNow.find((r) => r.id === id)?.text !== rulesThen.find((r) => r.id === id)?.text);
  const casesMoved = !sameJson(contractUseCasesOf(env, envState).map((u) => u.text), contractUseCasesOf(env, then).map((u) => u.text));
  /* Versions made before settings were kept with them can't restore them —
     said, never guessed. */
  const config = target.config || null;
  const settingsMoved = config ? WORLD_CONFIG_KEYS.filter((k) => !sameJson(config[k], envState?.[k])) : [];
  const discards = config ? pendingEnvChanges(envState).length : 0;

  const changes = [
    gone.length && `The world stops answering ${gone.join(", ")}`,
    back.length && `The world answers ${back.join(", ")} again`,
    ruleMoves.length && `${plural(ruleMoves.length, "rule")} ${ruleMoves.length === 1 ? "reads" : "read"} as in ${name(target)} (${ruleMoves.join(", ")})`,
    casesMoved && `Use cases read as in ${name(target)}`,
    settingsMoved.length && `Tool behaviour, actors and seed as in ${name(target)}`,
    !config && "Tool behaviour, actors and seed stay as they are — that version didn't keep them",
  ].filter(Boolean);

  /* Scenarios whose proof holds in the older line keep it; the rest are
     re-checked, and the ones needing a tool the world drops sit out. */
  const thenLine = envLineage(env, envState, target.label);
  const holds = (row) => (row.provedOn || [provedAgainst(row, env, envState)]).some((l) => l && thenLine.has(l));
  const live = (envState?.scenarios || []).filter((r) => !r.retired && !r.archived);
  const recheck = live.filter((r) => !holds(r)).length;
  const sitOut = live.filter((r) => requiredToolsOf(r, knownToolsFor(env, envState)).some((t) => gone.includes(t))).length;

  const patch = (latest = envState) => {
    const base = config ? { ...latest, unsavedEnvChanges: [] } : latest;
    const minted = {
      ...nextEnvVersion(env, base, {
        from: target.label,
        revertedTo: target.label,
        changed: [
          (gone.length || back.length || casesMoved) && "contract",
          ruleMoves.length && "rules",
          settingsMoved.length && "toolbehaviour",
        ].filter(Boolean),
        note: `Rolled back to ${name(target)}'s world and contract.`,
      }),
      ...(config && { config }),
    };
    const stored = latest?.envVersions?.length ? latest.envVersions : [...environmentVersions(env, latest)].reverse();
    const scenarios = (latest?.scenarios || []).map((row) => {
      if (row.retired || row.archived || !holds(row)) return row;
      const on = row.provedOn || [provedAgainst(row, env, latest)];
      return { ...row, provedOn: [...new Set([...on.filter(Boolean), minted.label])] };
    });
    return {
      envVersions: [...stored, minted],
      activeEnvVersion: minted.label,
      unsavedEnvChanges: [],
      scenarios,
      ...(config && Object.fromEntries(WORLD_CONFIG_KEYS.map((k) => [k, config[k]]))),
    };
  };

  return { ok: true, target, targetName: name(target), next: nextSnapshotLabel(env, envState), changes, discards, recheck, sitOut, patch };
};

/* ── rolling back a whole environment version ─────────────────────────────── */

/*
  People see one "environment vN" — world and contract, scenario set,
  sub-goals, evaluations and simulated caller together — so rolling back to
  one restores all of it, as a NEW version (the way a revert works): nothing
  is deleted, numbering only moves forward, and every run keeps naming what
  it ran on.

    world       an older world → rollbackPlan above (tools, rules, use cases,
                tool behaviour, actors, seed)
    scenarios   added since → archived (listed, never deleted); archived since
                → restored; edited since → their wording then, as a new
                scenario version
    sub-goals   published since → a revert to the criteria then
    evaluations the list then (from the change log; a version from before the
                log kept lists can't be restored — said, never guessed)
    caller      the model then

  { ok, error?, snap, next, changes, keeps, recheck, sitOut, discards, patch(latest) }
*/
const msOf = (iso) => Date.parse(iso || "") || 0;
const rowName = (r) => r.title || r.name || "scenario";
const evalName = (e) => (typeof e === "string" ? resolveEval(e)?.name || e : e?.name || resolveEval(e)?.name || e?.id);
const named = (rows, max = 3) => {
  const names = rows.slice(0, max).map(rowName);
  return rows.length > max ? `${names.join(", ")} and ${rows.length - max} more` : names.join(", ");
};

/* The applied evaluations at a moment: the list the first later change
   replaced, or the list the last earlier change left. `undefined` = nothing
   changed since; `null` = changed since, but the list then wasn't kept. */
const evalsAt = (envState, until) => {
  const log = [...(envState?.evalChanges || [])].sort((a, b) => msOf(a.at) - msOf(b.at));
  const later = log.filter((c) => msOf(c.at) > until);
  if (!later.length) return undefined;
  if (later[0].before) return later[0].before;
  const earlier = log.filter((c) => msOf(c.at) <= until).pop();
  return earlier?.evals || null;
};

export const snapshotRollbackPlan = (env, envState, snapLabel) => {
  const list = envSnapshotsOf(env, envState);
  const snap = list.find((s) => s.label === snapLabel);
  const current = currentEnvSnapshot(env, envState);
  if (!snap || !current || snap.label === current.label) return { ok: false, error: "That's the version in use." };
  const name = `environment ${snap.label}`;
  /* Everything made after this snapshot's own changes is newer than it —
     half the gap the snapshot folding leaves between two actions. */
  const until = (snap.endMs || 0) + SAME_ACTION_MS / 2;

  /* World and contract — only when it's an older world. */
  const worldNow = currentEnvVersion(env, envState)?.label;
  const world = snap.parts.env && snap.parts.env !== worldNow ? rollbackPlan(env, envState, snap.parts.env, { displayName: name }) : null;
  const worldOk = world?.ok ? world : null;

  /* Scenario set membership and wording. Proposals aren't in the set. */
  const rows = (envState?.scenarios || []).filter((r) => !r.proposed);
  const leave = rows.filter((r) => !archivedOf(r) && msOf(r.addedAt) > until);
  const comeBack = rows.filter((r) => archivedOf(r) && msOf(archivedOf(r).at) > until && !(msOf(r.addedAt) > until));
  const leaving = new Set(leave.map((r) => r.id));
  const editedSince = rows.filter((r) => !leaving.has(r.id) && (r.versionLog || []).some((e) => msOf(e.at) > until));
  const firstLaterEdit = (r) => (r.versionLog || []).find((e) => msOf(e.at) > until);
  const reword = editedSince.filter((r) => firstLaterEdit(r)?.snapshot);
  const keepWording = editedSince.filter((r) => !firstLaterEdit(r)?.snapshot);

  /* Sub-goals published since go back to their criteria then. */
  const edits = envState?.subGoalEdits || [];
  const goalIds = [...new Set(edits.filter((e) => !e.shadow && msOf(e.at) > until).map((e) => e.id))];
  const goalName = (id) => [...edits].reverse().find((e) => e.id === id)?.name || id.replace(/^SG-(tool-|rule-)?/, "");

  /* Evaluations and caller. */
  const evalsThen = evalsAt(envState, until);
  const evalsMove = Array.isArray(evalsThen) && !sameJson(evalsThen, envState?.evals || []);
  const callerThen = snap.parts.caller;
  const callerNow = simulatorOf(env, envState).model;
  const callerMoves = !!callerThen && callerThen !== callerNow;

  const changes = [
    ...(worldOk ? worldOk.changes : []),
    !worldOk && (world ? null : "World and contract stay — this version uses the same world"),
    leave.length && `${plural(leave.length, "scenario")} added since ${leave.length === 1 ? "leaves" : "leave"} the set (archived, not deleted): ${named(leave)}`,
    comeBack.length && `${plural(comeBack.length, "scenario")} archived since ${comeBack.length === 1 ? "comes" : "come"} back: ${named(comeBack)}`,
    reword.length && `${plural(reword.length, "scenario")} edited since ${reword.length === 1 ? "goes" : "go"} back to ${name}'s wording, as a new scenario version: ${named(reword)}`,
    goalIds.length && `${plural(goalIds.length, "sub-goal")} ${goalIds.length === 1 ? "goes" : "go"} back to ${name}'s criteria: ${goalIds.map(goalName).slice(0, 3).join(", ")}`,
    evalsMove && `Evaluations go back to ${evalsThen.length ? evalsThen.map(evalName).join(", ") : "none"}`,
    callerMoves && `Simulated caller → ${callerThen}`,
  ].filter(Boolean);
  const keeps = [
    keepWording.length && `${plural(keepWording.length, "edited scenario")} ${keepWording.length === 1 ? "keeps its" : "keep their"} current wording — ${name} didn't keep the earlier one`,
    evalsThen === null && `Evaluations stay as they are — ${name} didn't keep its list`,
  ].filter(Boolean);
  const moves = worldOk || leave.length || comeBack.length || reword.length || goalIds.length || evalsMove || callerMoves;
  if (!moves) return { ok: false, error: `Nothing differs from ${name} that can be restored.` };

  const patch = (latest = envState) => {
    const now = new Date().toISOString();
    let out = worldOk ? worldOk.patch(latest) : {};
    let working = { ...latest, ...out };
    const merge = (p) => { out = { ...out, ...p }; working = { ...working, ...p }; };

    /* Scenarios: wording first, then membership. */
    const leaveIds = new Set(leave.map((r) => r.id));
    const backIds = new Set(comeBack.map((r) => r.id));
    const rewordIds = new Set(reword.map((r) => r.id));
    let bump = null;
    const scenarios = (working.scenarios || []).map((r) => {
      let row = r;
      if (rewordIds.has(r.id)) {
        const later = (r.versionLog || []).filter((e) => msOf(e.at) > until);
        const level = later.reduce((m, e) => largerBump(m, e.level), null) || "major";
        bump = largerBump(bump, level);
        // eslint-disable-next-line no-unused-vars
        const { v: _v, versionLog: _log, ...then } = firstLaterEdit(r).snapshot;
        row = bumpScenario({ ...r, ...then, versionLog: r.versionLog, archived: r.archived }, level, `Rolled back to ${name}`, r);
      }
      if (leaveIds.has(r.id)) row = archiveScenario(row, `Rolled back to ${name}`);
      if (backIds.has(r.id)) row = restoreScenario(row);
      return row;
    });
    if (leave.length || comeBack.length || reword.length) {
      merge(suiteLogPatch(working, { scenarios }, {
        summary: `Rolled back to ${name}'s scenarios — ${[
          leave.length && `${leave.length} archived`,
          comeBack.length && `${comeBack.length} restored`,
          reword.length && `${reword.length} reworded`,
        ].filter(Boolean).join(", ")}`,
        largestBump: bump,
      }));
    }

    /* Sub-goals: a revert each, never an edit in place. */
    if (goalIds.length) {
      const all = working.subGoalEdits || [];
      const first = Number(nextCatalogVersion(working).slice(1));
      const reverts = goalIds.map((id, i) => {
        const then = all.filter((e) => e.id === id && !e.shadow && msOf(e.at) <= until).pop();
        return {
          id, name: then?.name || null, criterion: then?.criterion, kind: "revert",
          n: subGoalVersionOf(working, id) + 1, version: `c${first + i}`,
          note: `Rolled back to ${name}`, at: now,
        };
      });
      merge({ subGoalEdits: [...all, ...reverts] });
    }

    if (evalsMove) {
      merge({
        evals: evalsThen,
        evalChanges: [...(working.evalChanges || []), { detail: `Rolled back to ${name}'s evaluations`, at: now, before: working.evals || [], evals: evalsThen }],
      });
    }

    if (callerMoves) merge(callerChangePatch(env, working, callerThen));
    return out;
  };

  return {
    ok: true,
    snap,
    next: nextSnapshotLabel(env, envState),
    changes,
    keeps,
    recheck: worldOk?.recheck || 0,
    sitOut: worldOk?.sitOut || 0,
    discards: worldOk?.discards || 0,
    patch,
  };
};
