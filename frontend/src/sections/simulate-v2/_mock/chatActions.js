/*
  The builder chat, acting (PRD v6 Story 15: AC-15.2, 15.5, 15.6, 15.8,
  15.11, 15.12).

  A message either asks for a change to the environment or asks a question
  about it. Changes go through the same patch helpers the panels use, so a
  tier set from the chat is the tier the World tab shows, and the version
  bar counts it the same way. Questions are answered from the environment's
  own data — its tools and their tiers, the latest run's verdicts, the open
  setup gaps — never from a canned reply.

  `parseChatIntent` is pure and knows nothing about React: it maps a message
  to an intent, or null when the message isn't one of these, in which case
  the workspace's own reply handles it as before.
*/
import { TIERS, FAULT_PRESETS, tierOf, toolEnvOf, toolEnvSummary, toolSettingOf, faultLine, faultPresetOf, liveAllowedOf, mcpLiveTargetOf } from "./toolEnv";
import { withPinnedWorld } from "./toolFit";
import { rebuildRun } from "./comparison";
import { setupGaps, sortGaps, runReadiness, missingCredentials, credentialManifest } from "./setupGaps";
import { faultReason } from "./failures";
import { getEval } from "./evals";
import { isConnectOnly } from "./envModel";
import { envVarsOf } from "./envConfig";
import { scenarioStepOf, versionCardOf } from "./versionCard";
import { sideEffectOf } from "./toolEnv";
import { toolArgsOf, contractUseCasesOf } from "./rlContract";

const clean = (s) => String(s || "").replace(/[`"“”‘’]/g, "").replace(/\s+/g, " ").trim();
const words = (s) => clean(s).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ").filter(Boolean);

/* ── which tool, which tier ─────────────────────────────────────────────── */

const toolNamed = (text, tools) => {
  const t = clean(text).toLowerCase();
  return tools.find((tool) => new RegExp(`\\b${tool.name.toLowerCase()}\\b`).test(t))
    || tools.find((tool) => t.includes(tool.name.toLowerCase().replace(/_/g, " ")))
    || null;
};

const TIER_WORDS = [
  ["live", /\blive\b|\breal tool\b/],
  ["stateful", /\bstateful\b/],
  ["replay", /\breplay(ed)?\b|\brecord(ed)?( and| &) replay\b/],
  ["stub", /\bstub(bed)?\b|\bcanned\b/],
];
const tierIn = (t) => (TIER_WORDS.find(([, re]) => re.test(t)) || [])[0] || null;

const PRESET_WORDS = [
  ["adversarial", /\badversarial\b|\bheavy\b|\bharsh\b/],
  ["realistic", /\brealistic\b|\bsome\b|\btypical\b/],
  ["none", /\b(no|remove|clear|drop|turn off|without|clean)\b/],
];
const presetIn = (t) => (PRESET_WORDS.find(([, re]) => re.test(t)) || [])[0] || null;

/* ── which evaluation ───────────────────────────────────────────────────── */

const EVAL_WORDS = [
  ["compliance", /\b(compliance|compliant|regulatory|regulation)\b/],
  ["pii_leakage", /\b(pii|privacy|personal data|data leak)\b/],
  ["hallucination", /\bhallucinat/],
  ["tool_correctness", /\btool[- ]?(choice|call|calls|use|usage|correctness)\b/],
  ["escalation_accuracy", /\bescalat/],
  ["tone", /\b(tone|professional)/],
  ["empathy", /\bempath/],
  ["latency", /\b(latency|response time|slow)\b/],
  ["interruption", /\binterrupt/],
  ["context_carryover", /\b(context|carryover|carry over)\b/],
  ["safety", /\bsafety\b/],
  ["policy_adherence", /\bpolicy\b/],
  ["task_success", /\btask success\b/],
];

/* ── which secret ───────────────────────────────────────────────────────── */

const PROVIDER_WORDS = ["openai", "vapi", "retell", "bland", "elevenlabs", "livekit", "deepgram", "vertex", "observability", "endpoint", "browser"];

/**
 * What a message asks for. `ctx` = { env, envState }; tool names are read
 * from the world the environment is pinned to.
 */
export const parseChatIntent = (text, ctx = {}) => {
  const raw = clean(text);
  const t = raw.toLowerCase();
  if (!t) return null;
  /* A correction to the contract is the workspace chat's to handle — it
     states the version cost and asks first (parseContractIntent, below). */
  if (parseContractIntent(text, ctx)) return null;
  /* Nudges are the user's to answer (PRD v7 AC-15.4b). Asked to approve,
     dismiss or answer one — or the fix-or-policy question — the chat never
     does it: it lists what's open, links to the inbox and waits. */
  if (/\bnudges?\b|fix[- ]or[- ]policy|\bpolicy change\b|\bnot intended\b|\bnot applicable\b|\b(approve|accept|dismiss)\b.*\b(fork|backfill|handler)\b/.test(t)) {
    return { kind: "missing", nudgeGuard: true };
  }
  const tools = withPinnedWorld(ctx.env, ctx.envState)?.tools || ctx.env?.tools || [];
  const tool = toolNamed(raw, tools);
  const everyTool = /\b(every|all( the)?|each) tools?\b/.test(t);

  /* Faults on a tool — "Add realistic faults to lookup_order". */
  if (/\bfaults?\b|\bfault profile\b/.test(t) && (tool || everyTool)) {
    const preset = presetIn(t) || (/\badd\b/.test(t) ? "realistic" : null);
    if (preset) return { kind: "setFaults", tool: tool?.name || null, all: !tool && everyTool, preset };
  }

  /* A tier — "Use live for shipment_status", "Keep issue_refund stateful". */
  const tier = tierIn(t);
  if (tool && tier && /\b(use|set|switch|make|keep|put|run|move|change|go|leave|turn)\b/.test(t)) {
    return { kind: "setTier", tool: tool.name, tier, keep: /\b(keep|leave)\b/.test(t) };
  }

  /* The trace of a failing scenario. */
  if (/\btraces?\b/.test(t) && /\b(show|open|see|where|view|trace for|give)\b/.test(t)) {
    const m = raw.match(/trace (?:for|of) (.+?)[?.!]*$/i);
    const q = m && !/^(the )?(failing|failed|broken) ?(one|scenario|case)?$/i.test(m[1].trim()) && !/^(it|that|this)$/i.test(m[1].trim()) ? m[1].trim() : null;
    return { kind: "showTrace", query: q };
  }

  /* Why a scenario failed. */
  if (/\bwhy\b/.test(t) && /\b(fail|fails|failed|failing|break|broke|broken)\b/.test(t)) {
    const m = raw.match(/why (?:did|does|do|is|was|has) (.+?) (?:fail|failing|failed|break|broke)/i);
    const q = m ? m[1].replace(/^(the|my|this|that)\s+/i, "").replace(/\s+scenario$/i, "").trim() : null;
    return { kind: "whyFailed", query: q && !/^(it|that|this|the run|run|everything|they|those)$/i.test(q) ? q : null };
  }

  /* What stands between this environment and a run. */
  if (/(what'?s|what is|anything) (still )?(missing|left|blocking|outstanding)|before (we|i) can run|can (we|i) run|ready to run|what('?s| is) (stopping|holding)/.test(t)) {
    return { kind: "missing" };
  }

  /* Which tools the builder found. */
  if (/\b(what|which) tools\b|tools (did|have) you (find|found|read|see)|\blist (the |all |every )?tools\b|\bshow (me )?(the |all )?tools\b|\btools you found\b/.test(t)) {
    return { kind: "listTools" };
  }

  /* An evaluation from the catalogue — "Add a compliance judge". */
  if (/\b(add|include|attach|turn on)\b/.test(t) && /\b(judge|eval|evals|evaluation|grader|check|scorer)\b/.test(t)) {
    const hit = EVAL_WORDS.find(([, re]) => re.test(t));
    if (hit && getEval(hit[0])) return { kind: "addEval", evalId: hit[0] };
  }

  /* A secret's reference — "Rotate my OpenAI key". */
  if (/\b(rotate|change|update|set|replace|point|swap)\b/.test(t) && /\b(key|token|secret|credential|reference)\b/.test(t)) {
    const provider = PROVIDER_WORDS.find((p) => t.includes(p)) || null;
    return { kind: "setSecret", provider };
  }

  /* Which variables the environment passes in. */
  if (/\benv(ironment)? ?var(iable)?s?\b/.test(t)) {
    return { kind: "envVars", usedBy: /\bgrader|judge/.test(t) ? "grader" : null };
  }

  return null;
};

/* ── correcting the contract (PRD v7) ───────────────────────────────────── */

/*
  "Add a rule: never quote a delivery date the carrier hasn't given",
  "Change R2 to …", "Remove rule R3", "Add a use case: …", "Reword use case
  UC-4fxq to …", "Allow account_number for lookup_order by", "Cap
  issue_refund amount at 500", "Set issue_voucher amount to 0 to 100".
  Each maps to a `contractEditPlan` edit (rlContract.js); the workspace chat
  shows the version cost line first and asks before anything lands.
  Also: asking for Live on a tool that writes is refused here, since Live is
  only for read-only tools.
*/
const unquote = (s) => String(s || "").trim().replace(/^(that|which|saying|to say)\s+/i, "").replace(/^[:\-–\s]+/, "").replace(/^["“'‘]|["”'’.]+$/g, "").trim();
const valuesIn = (s) => String(s || "")
  .replace(/\b(and|or)\b/gi, ",")
  .split(/[,/]+/)
  .map((v) => v.trim().replace(/^["“'‘`]|["”'’`]$/g, "").replace(/\s+/g, "_"))
  .filter((v) => v && !/^(the|a|an|value|values)$/i.test(v));
const num = (s) => Number(String(s).replace(/[£$,]/g, ""));

export const parseContractIntent = (text, ctx = {}) => {
  const raw = String(text || "").replace(/\s+/g, " ").trim();
  const t = raw.toLowerCase();
  if (!t) return null;

  /* Rules — by id (R1…), or new. */
  let m = raw.match(/\b(?:add|create|new)\b(?: an?| one more)?(?: hard)? rule\b(.*)$/i);
  if (m && unquote(m[1])) return { kind: "contract", edit: { target: "rule", op: "add", text: unquote(m[1]) } };
  m = raw.match(/\b(?:remove|delete|drop|retire)\b (?:the )?(?:rule )?(R\d+)\b/i);
  if (m) return { kind: "contract", edit: { target: "rule", op: "remove", id: m[1].toUpperCase() } };
  m = raw.match(/\b(?:change|edit|reword|update|rewrite|replace|make)\b (?:the )?(?:rule )?(R\d+)\b(?: so it reads| to read| to| with| as)?(.*)$/i);
  if (m && unquote(m[2])) return { kind: "contract", edit: { target: "rule", op: "edit", id: m[1].toUpperCase(), text: unquote(m[2]) } };

  /* Use cases — by id (UC-…), or by number in the list. */
  const caseRef = (ref) => {
    if (/^uc-/i.test(ref)) return `UC-${ref.slice(3).toLowerCase()}`;
    const list = ctx.env ? contractUseCasesOf(ctx.env, ctx.envState) : [];
    return list[Number(ref) - 1]?.id || ref;
  };
  m = raw.match(/\badd\b(?: an?)? use ?case\b(.*)$/i);
  if (m && unquote(m[1])) return { kind: "contract", edit: { target: "useCase", op: "add", text: unquote(m[1]) } };
  m = raw.match(/\b(?:remove|delete|drop)\b (?:the )?use ?case (UC-[a-z0-9]+|\d+)\b/i);
  if (m) return { kind: "contract", edit: { target: "useCase", op: "remove", id: caseRef(m[1]) } };
  m = raw.match(/\b(?:change|edit|reword|rename|update|rewrite)\b (?:the )?use ?case (UC-[a-z0-9]+|\d+)\b(?: to read| to| as)?(.*)$/i);
  if (m && unquote(m[2])) return { kind: "contract", edit: { target: "useCase", op: "edit", id: caseRef(m[1]), text: unquote(m[2]) } };

  /* A tool argument's allowed values. */
  const tools = withPinnedWorld(ctx.env, ctx.envState)?.tools || ctx.env?.tools || [];
  const tool = toolNamed(raw, tools);
  if (!tool) return null;

  /* Live is only for read-only tools. */
  if (tierIn(t) === "live" && sideEffectOf(tool) === "write" && /\b(use|set|switch|make|put|run|move|change|go|turn)\b/.test(t)) {
    return { kind: "liveRefused", tool: tool.name };
  }

  /* Faults and tiers are the tool dial's, not the contract's. */
  if (/\bfaults?\b|\bfault profile\b/.test(t) || (tierIn(t) && !/\ballow(ed|s)?\b.*\bvalues?\b/.test(t))) return null;
  const args = toolArgsOf(tool, ctx.envState);
  const editable = args.filter((a) => a.type === "enum" || a.min != null || a.max != null);
  const rest = t.replace(new RegExp(tool.name, "gi"), " ");
  const word = (w) => new RegExp(`(^|[^a-z0-9_])${w}([^a-z0-9_]|$)`, "i").test(rest);
  /* By its name, its name in words, or its first word when no other argument shares it ("amount" → amount_cents). */
  const named = args.find((a) => word(a.name) || (a.name.includes("_") && rest.includes(a.name.replace(/_/g, " "))))
    || args.find((a) => {
      const head = a.name.split("_")[0];
      return head.length > 2 && word(head) && args.filter((b) => b.name.split("_")[0] === head).length === 1;
    });
  const strong = /\b(allow|allowed|allows|accept|permit|disallow|forbid|values?|range|cap|capped|limit|max|maximum|min|minimum|up to|at most|at least|no more than|between)\b/.test(t);
  const arg = named || (strong && editable.length === 1 ? editable[0] : null);
  const aboutValues = strong || (named && /\b(add|remove|drop|set|change|only|make)\b/.test(t));
  if (!arg || !aboutValues) return null;
  const base = { target: "toolArg", tool: tool.name, arg: arg.name };
  const rangeM = t.match(/(?:between |from )?£?(\d+(?:\.\d+)?)\s*(?:to|-|–|and)\s*£?(\d+(?:\.\d+)?)/);
  const maxM = t.match(/\b(?:up to|at most|no more than|max(?:imum)?(?: of)?|cap(?:ped)?\b(?:.*?\bat)?|limit(?:ed)?\b(?:.*?\bto)?)\s*£?(\d+(?:\.\d+)?)/);
  const minM = t.match(/\b(?:at least|min(?:imum)?(?: of)?|no less than)\s*£?(\d+(?:\.\d+)?)/);
  if (arg.type !== "enum" && arg.type !== "bool") {
    if (rangeM) return { kind: "contract", edit: { ...base, min: num(rangeM[1]), max: num(rangeM[2]) } };
    if (maxM || minM) return { kind: "contract", edit: { ...base, ...(maxM && { max: num(maxM[1]) }), ...(minM && { min: num(minM[1]) }) } };
    return null;
  }
  const strip = (s) => s.replace(new RegExp(`\\b(${tool.name}|${arg.name}|${tool.name.replace(/_/g, " ")}|${arg.name.replace(/_/g, " ")})\\b('s)?`, "gi"), " ").replace(/\b(the|tool|argument|arg|field|parameter)\b/gi, " ");
  m = raw.match(/\b(?:disallow|forbid|stop allowing|no longer allow|remove|drop)\b(.+?)\b(?:from|for|on|in|as)\b/i);
  if (m && valuesIn(strip(m[1])).length) return { kind: "contract", edit: { ...base, remove: valuesIn(strip(m[1])) } };
  m = raw.match(/\bonly (?:allow|accept)\b(.+?)(?:\b(?:for|on|in|as)\b|$)/i)
    || raw.match(/\b(?:allowed values?|values?)\b.*?\b(?:to|are|=|:)\s+(.+)$/i)
    || raw.match(/\bset\b.*?\bto\b(.+)$/i);
  if (m && valuesIn(strip(m[1])).length) return { kind: "contract", edit: { ...base, values: valuesIn(strip(m[1])) } };
  m = raw.match(/\b(?:allow|add|accept|permit)\b(.+?)\b(?:to|for|on|in|as)\b/i);
  if (m && valuesIn(strip(m[1])).length) return { kind: "contract", edit: { ...base, add: valuesIn(strip(m[1])) } };
  return null;
};

/* ── grounded answers ───────────────────────────────────────────────────── */

/** The tools the world answers, each with the tier and faults it runs at. */
export const toolsAnswer = (env, envState) => {
  const world = withPinnedWorld(env, envState);
  const connectOnly = isConnectOnly(env);
  const rows = connectOnly
    ? (world?.tools || []).map((tool) => ({ name: tool.name, desc: tool.desc || "", tier: null, faults: null }))
    : toolEnvOf(world, envState).map((s) => {
      const tool = (world?.tools || []).find((x) => x.name === s.name);
      return {
        name: s.name,
        desc: tool?.desc || "",
        tier: tierOf(s.tier).word,
        tierId: s.tier,
        isDefault: s.tier === s.defaultTier,
        faults: faultPresetOf(s.faults) === "none" ? null : faultLine(s.faults),
      };
    });
  return {
    rows,
    connectOnly,
    summary: connectOnly ? "Answered by the agent's own systems" : toolEnvSummary(world, envState),
  };
};

/** Finished runs, newest first. */
export const finishedRuns = (envState) => (envState?.runs || [])
  .filter((r) => !r.pending && !r.synthetic && r.finishedAt && r.status !== "running")
  .sort((a, b) => Date.parse(b.finishedAt) - Date.parse(a.finishedAt));

/* Human-readable first — `name` is the scenario's slug (persona + rule), not something to say. */
const nameOf = (x) => x?.title || x?.name || x?.id || "scenario";

/** The one line a failing scenario's verdict comes down to. */
export const verdictLineOf = (task) => {
  if (!task) return null;
  if (task.faultFailed) {
    const hit = (task.toolFaults || []).find((h) => h.outcome === "failed") || (task.toolFaults || [])[0];
    return { source: "Injected fault", line: hit?.note || "Lost to an injected tool fault." };
  }
  if (task.measured === false) return { source: "Not counted · test invalid or error", line: faultReason(task) || "Nothing upstream of the agent held, so there is no verdict." };
  /* Sub-goal rule: the turn limit, or a required evaluation, when the
     critical sub-goals didn't fail it themselves. */
  if (task.turnLimit) return { source: "Turn limit", line: `Hit the turn limit (${task.turnLimit.limit}) — the conversation was stopped there.` };
  if (task.failedBy === "required-evaluation") {
    const req = (task.evalResults || []).find((e) => !e.passed && e.id === task.requiredFail?.id);
    return { source: `Required evaluation · ${task.requiredFail?.name || "required"}`, line: req?.reason || "A required evaluation failed; every critical sub-goal held." };
  }
  const bad = (task.evalResults || []).find((e) => !e.passed && !e.reportedOnly);
  if (bad) return { source: bad.name || bad.id, line: bad.reason };
  return { source: "Samples", line: `Passed ${task.passes ?? 0} of ${task.repeats || 0} samples.` };
};

const rank = (t) => ({ failed: 0, flaky: 1, unmeasured: 2 }[t.status] ?? 3);

/** Rebuilt tasks of a run, worst first — failed, then flaky. */
export const failingOf = (env, envState, run) => {
  if (!run) return [];
  return rebuildRun(env, envState, run)
    .filter((t) => t.status === "failed" || t.status === "flaky")
    /* Release blockers first; then the ones a fault cost — the newest cause. */
    .sort((a, b) => rank(a) - rank(b) || (b.critical ? 1 : 0) - (a.critical ? 1 : 0) || (b.faultFailed ? 1 : 0) - (a.faultFailed ? 1 : 0));
};

/* Best match for a scenario named loosely — its slug, title or summary.
   Variants share a title, so ties go to one in `prefer` (the failing ones). */
const matchScenario = (rows, query, prefer = null) => {
  if (!query) return null;
  const q = words(query).filter((w) => !["the", "a", "an", "one", "case", "scenario", "caller", "call"].includes(w));
  if (!q.length) return null;
  const scored = rows.map((r) => {
    const hay = words(`${r.name || ""} ${r.title || ""} ${r.summary || ""} ${r.id || ""}`);
    const exact = words(r.name || "").join(" ") === q.join(" ") ? 100 : 0;
    return { r, score: exact + q.filter((w) => hay.includes(w)).length / q.length + (prefer?.has(r.id) ? 0.01 : 0) };
  }).filter((x) => x.score >= 0.5).sort((a, b) => b.score - a.score);
  return scored[0]?.r || null;
};

/**
 * Why a scenario failed, from its latest run that ran it. With no scenario
 * named, the worst one in the latest run.
 */
export const failureAnswer = (env, envState, query) => {
  const runs = finishedRuns(envState);
  if (!runs.length) return { empty: "No run has finished in this environment yet — nothing has failed." };
  const scenarios = (envState?.scenarios || []).filter((r) => !r.retired);
  const named = matchScenario(scenarios, query, query ? new Set(failingOf(env, envState, runs[0]).map((t) => t.id)) : null);
  if (query && !named) return { empty: `No scenario here matches "${query}".`, query };
  const run = named ? runs.find((r) => !r.scenarioIds || r.scenarioIds.includes(named.id)) : runs[0];
  if (!run) return { empty: `${nameOf(named)} hasn't been in a finished run yet.`, scenario: named };
  const tasks = rebuildRun(env, envState, run);
  const task = named ? tasks.find((t) => t.id === named.id) : failingOf(env, envState, run)[0];
  if (!task) return { empty: named ? `${nameOf(named)} wasn't in run ${run.ordinal || ""} — it was added after.` : `Nothing failed in run ${run.ordinal || ""}.`, run };
  return {
    run,
    task,
    scenarioId: task.id,
    name: nameOf(task),
    summary: task.summary || task.title || "",
    status: task.status,
    samples: task.measured === false ? null : `${task.passes ?? 0} of ${task.repeats || 0} samples passed`,
    verdict: task.status === "passed" ? null : verdictLineOf(task),
    faults: (task.toolFaults || []).map((h) => ({ tool: h.tool, note: h.note, outcome: h.outcome })),
    passed: task.status === "passed",
  };
};

/** What still has to happen before a run can start — and what only costs coverage. */
export const missingAnswer = (env, envState) => {
  const readiness = runReadiness(envState);
  const gaps = sortGaps(setupGaps(env, envState));
  const open = gaps.filter((g) => g.status === "blocking" || g.status === "optional");
  const creds = missingCredentials(env, envState);
  /* An agent change is one card, listed — never answered from here — and
     linked to Needs input (AC-15.4b). It doesn't block a run. */
  const card = versionCardOf(envState);
  const step = scenarioStepOf(envState);
  const nudges = [
    card?.status === "open" && { id: "nudges", title: `Agent ${card.newest} changed`, area: "Review · rebuild or skip" },
    step?.status === "open" && { id: "nudges", title: "Scenarios to update", area: "Review · after the rebuild" },
  ].filter(Boolean);
  return {
    ready: readiness.ready,
    reason: readiness.reason,
    nudges,
    blocking: open.filter((g) => g.status === "blocking").map((g) => ({ id: g.id, title: g.title, area: g.area })),
    optional: [...nudges, ...open.filter((g) => g.status === "optional").map((g) => ({ id: g.id, title: g.title, area: g.area }))],
    credentials: creds.map((c) => ({ id: c.id, purpose: c.purpose })),
  };
};

/** The latest run's worst scenario, for its trace. */
export const traceAnswer = (env, envState, query) => {
  if (query) {
    const a = failureAnswer(env, envState, query);
    return a.empty ? a : { run: a.run, scenarioId: a.scenarioId, name: a.name, status: a.status, verdict: a.verdict };
  }
  const run = finishedRuns(envState)[0];
  if (!run) return { empty: "No run has finished yet, so there is no trace to open." };
  const task = failingOf(env, envState, run)[0];
  if (!task) return { empty: `Every scenario in run ${run.ordinal || ""} passed — no failing trace to open.`, run };
  return { run, scenarioId: task.id, name: nameOf(task), status: task.status, verdict: verdictLineOf(task) };
};

/* ── changes ────────────────────────────────────────────────────────────── */

/** What setting one tool's tier would do. */
export const tierPlan = (env, envState, toolName, tier) => {
  const world = withPinnedWorld(env, envState);
  const tool = (world?.tools || []).find((t) => t.name === toolName);
  if (!tool) return null;
  const setting = toolSettingOf(tool, envState);
  return {
    tool: toolName,
    setting,
    from: tierOf(setting.tier).word,
    to: tierOf(tier).word,
    tier,
    same: setting.tier === tier,
    /* Whether Live may be chosen at all — writes never, a Gateway block never. */
    live: tier === "live" ? liveAllowedOf(setting) : null,
    /* Live on a tool whose default isn't Live points a run at a real API —
       unless it's from an MCP server, whose server (or Gateway) is the target. */
    needsTarget: tier === "live" && setting.defaultTier !== "live" && !setting.liveTarget,
    target: mcpLiveTargetOf(tool),
    toolRecord: tool,
  };
};

export const faultPlan = (env, envState, toolName, preset) => {
  const world = withPinnedWorld(env, envState);
  const tool = (world?.tools || []).find((t) => t.name === toolName);
  if (!tool) return null;
  const setting = toolSettingOf(tool, envState);
  const faults = FAULT_PRESETS[preset]?.faults || null;
  return {
    tool: toolName,
    preset,
    label: FAULT_PRESETS[preset]?.label || "Clean",
    faults,
    line: faults ? faultLine(faults) : "No faults",
    same: faultPresetOf(setting.faults) === preset,
  };
};

/** The secret a "rotate" message is about: an env var or a manifest entry. */
export const secretTarget = (env, envState, provider) => {
  const vars = envVarsOf(envState).filter((v) => v.secret);
  const creds = credentialManifest(env, envState).filter((c) => !c.satisfiedBy);
  if (provider) {
    const v = vars.find((x) => x.key.toLowerCase().includes(provider));
    if (v) return { type: "env", id: v.key, ref: v.ref, provider };
    const c = creds.find((x) => x.id.includes(provider) || x.provider === provider);
    if (c) return { type: "cred", id: c.id, ref: c.ref, provider: c.provider, purpose: c.purpose };
  }
  const first = vars[0];
  return first ? { type: "env", id: first.key, ref: first.ref, provider: provider || null } : null;
};

export const envVarsAnswer = (envState, usedBy) => {
  const all = envVarsOf(envState);
  const rows = usedBy ? all.filter((v) => v.usedBy === usedBy) : all;
  return { rows, usedBy };
};

export { TIERS };
