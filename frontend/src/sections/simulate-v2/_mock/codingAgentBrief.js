/**
 * "Copy for coding agent" — a failure handed to Claude Code, Cursor or Codex
 * as one markdown brief.
 *
 * The coding agent can do the editing; what it cannot do is see what Future
 * AGI saw. So the brief carries the evidence, the agent's constraints and the
 * one step that turns its edit into a candidate — and says plainly what it
 * may not touch. A brief without the submit step produces a diff nobody
 * measures; a brief without the constraints produces one the run rejects.
 */
import { submitStepOf } from "./candidateRoute";
import { KIND_SURFACE } from "./optimizer";
import { limitRows, ceilingLabel, costCeilingLabel } from "./optimizationRuns";
import { changeFileFor } from "./optimize";
import { scrubPersonalData } from "./rsiModel";

/* What each surface reads as in a sentence — "Changes tool descriptions". */
const SURFACE_WORDS = {
  prompt: "prompts", tools: "tool descriptions", settings: "agent settings", code: "code", structure: "structure",
};

/* Fixes to the measurement — they mint an environment version, not an agent one. */
const ENV_KINDS = ["Verifier", "Reward spec"];

/* A protected entry is a path in the agent's own tree ("prompts/system.md");
   a change names it under the environment ("airline/prompts/system.md"). */
const isProtected = (path, list = []) => !!path && list.some((p) => path === p || path.endsWith(`/${p}`));

/**
 * Which of the agent's constraints a proposed fix touches.
 *
 * Not allowed is said, not hidden — the card stays, with the reason and the
 * way to change it.
 */
export const fixScope = (proposal, { env, limits } = {}) => {
  if (ENV_KINDS.includes(proposal?.kind)) {
    return { surface: "environment", environment: true, ok: true, checks: [], label: "Changes the environment's checks, not the agent" };
  }
  const surface = KIND_SURFACE[proposal?.kind] || "code";
  const path = changeFileFor(env, proposal)?.path || null;
  const locked = isProtected(path, limits?.protected);
  /* A model switch — a Model fix, or a setting that rewrites the model line —
     is held to the model lock, not to the allowed kinds of change: it's
     tried only when the lock names an approved list that has the model. */
  const switchTo = proposal?.model
    || (proposal?.diff || []).find((d) => d.type === "add" && /^model:/.test(d.text))?.text.replace(/^model:\s*/, "")
    || null;
  const switches = surface === "model" || !!switchTo;
  const approved = switches && limits?.model?.mode === "approved" && (limits.model.approved || []).includes(switchTo);
  const allowed = surface === "model" ? approved : (limits?.allowed || []).includes(surface);

  /* Each constraint the fix touches, as "Constraint: what it does — verdict".
     `blocked` rules the fix out; "checked when it runs" is measured, not
     guessed, so it never blocks. */
  const checks = [];
  if (surface !== "model") {
    checks.push({ id: "changes", constraint: "Allowed changes", says: SURFACE_WORDS[surface] || surface, verdict: allowed ? "allowed" : "not allowed", blocked: !allowed });
  }
  if (locked) checks.push({ id: "protected", constraint: "Protected parts", says: "edits", path, verdict: "not allowed", blocked: true });
  if (switches) {
    checks.push({
      id: "model", constraint: "Model lock",
      says: switchTo ? `switches the model to ${switchTo}` : "switches the model",
      verdict: approved ? "on the approved list" : "not allowed", blocked: !approved,
    });
  }
  /* What a model or setting change may cost or slow down is only known once
     it runs; the ceiling it's held to is the agent's own. */
  if (switches) {
    checks.push({ id: "cost", constraint: "Cost ceiling", ceiling: costCeilingLabel(limits).toLowerCase(), says: "likely adds cost", verdict: "checked when it runs" });
  } else if (surface === "settings" && proposal?.step === "turn_taking") {
    checks.push({ id: "latency", constraint: "Latency ceiling", ceiling: ceilingLabel(limits?.latencyCeiling ?? 0).toLowerCase(), says: "waits longer before answering", verdict: "checked when it runs" });
  } else if (surface === "settings") {
    /* Any other setting (temperature, retrieval) leaves the model alone. */
    checks.push({ id: "model", constraint: "Model lock", says: "keeps the model", verdict: "allowed" });
  }
  return {
    surface,
    path,
    allowed,
    protected: locked,
    model: switches ? { to: switchTo, approved } : null,
    checks,
    ok: !checks.some((c) => c.blocked),
    label: switches && surface === "model" ? "Switches the model" : `Changes ${SURFACE_WORDS[surface] || surface}`,
  };
};

/* Said to the coding agent as an instruction, not a status. */
const scopeNote = (s) => {
  if (!s) return "";
  if (s.environment) return `${s.label.toLowerCase()} — not yours to make; Future AGI applies it`;
  if (s.model && !s.model.approved) return `${s.label.toLowerCase()} — this agent's model is locked; do not change the model`;
  if (!s.allowed) return `${s.label.toLowerCase()} — not allowed for this agent; do not make this change`;
  if (s.protected) return `${s.label.toLowerCase()} — edits ${s.path}, which this agent protects; do not edit it`;
  const ceiling = (s.checks || []).find((c) => c.id === "cost" || c.id === "latency");
  return `${s.label.toLowerCase()} — allowed${ceiling ? `; ${ceiling.constraint.toLowerCase()} ${ceiling.ceiling}` : ""}`;
};

const asText = (x) => (typeof x === "string" ? x : x?.title || x?.text || "");

const OUTCOME_WORD = { fixed: "fixed", broke: "broke", "still-failing": "still failing", flaky: "flaky", inconclusive: "inconclusive", "off-script": "caller went off script — not counted", same: "unchanged" };

/**
 * The per-scenario breakdown a brief carries for a fix: each target
 * scenario, what measuring today found, and what the last run did to it.
 * Target scenarios only — the held-back set proves the change, so it never
 * reaches the improver.
 */
export const briefBreakdownOf = (record) => {
  const r = record?.result || null;
  const per = record?.reproduce?.perScenario || {};
  const last = r?.winner || (r?.trials || []).at(-1) || null;
  /* Before a search runs, the issue's scenarios stand in — less the held-back
     set's: its fresh variations, and any of the issue's own it held back. */
  const held = new Set([...(r?.held || []), ...(record?.pendingResult?.held || [])].map((t) => t.id));
  const tasks = r?.trainMeasuredTasks?.length
    ? r.trainMeasuredTasks
    : (record?.issueScenarios || []).filter((d) => !d.freshVariation && !held.has(d.id) && per[d.id]?.state !== "passed");
  return tasks.map((t) => {
    const p = per[t.id];
    const today = p ? (p.fails ? `failed ${p.fails} of ${p.of}` : `passed ${p.of} of ${p.of}`) : t.before === "failed" ? "failing" : t.before === "flaky" ? "flaky" : t.before || "—";
    const outcome = last?.perScenario?.[t.id];
    return { title: t.title, today, ...(outcome && { result: OUTCOME_WORD[outcome] || outcome }), ...(last && { run: `${r.winner ? "Kept" : "Last"} run · Run ${last.n}` }) };
  });
};
const bullets = (list) => list.map((x) => `- ${x}`);

/**
 * The brief, as markdown.
 *
 *   agent            { name, key }
 *   env              { id, name }
 *   issue            { title, severity?, id? } or a string
 *   rootCause        string
 *   suggestedFix     string, or fixes: { title, kind?, why?, diff?, scope? }
 *   evidence         strings, or { source, text } — the first five are used
 *   failingScenarios strings or { title }
 *   limits, model    the agent's constraints, as limitRows reads them
 *   submit           optional — replaces the default submit step
 *   runId            optional — the run the evidence came from
 *   proof            optional — { label, tierLabel } from proofKindOf
 *   members          optional — the members the change touches, by name
 *   names            optional — names the caller already knows (a caller or persona); others are caught by the scrubber itself
 *
 * Evidence comes from production: every excerpt goes through
 * scrubPersonalData before it is copied or handed to a coding agent (so do the root cause and scenario titles) — names,
 * numbers and addresses become realistic fake values — and the brief says so.
 */
export const codingAgentBrief = ({
  agent, env, issue, rootCause, suggestedFix, evidence = [], failingScenarios = [], limits, model, submit, runId, proof, members, names = [], pairing = null,
  diagnosis = null, breakdown = [],
}) => {
  const title = asText(issue) || "Failing scenarios";
  const severity = typeof issue === "object" ? issue?.severity : null;
  const agentKey = agent?.key || "<agent>";
  const envId = env?.id || "<environment>";

  const fixes = Array.isArray(suggestedFix) ? suggestedFix : null;
  const fixLines = fixes
    ? fixes.flatMap((f, i) => [
      `${i + 1}. **${f.title}**${f.kind ? ` (${f.kind}${f.scope ? `; ${scopeNote(f.scope)}` : ""})` : ""}`,
      ...(f.why ? [`   ${f.why}`] : []),
      ...(f.diff || []).map((d) => `   ${d.type === "remove" ? "-" : "+"} ${d.text}`),
    ])
    : [suggestedFix || "No fix proposed yet — start from the root cause."];

  /* The problem, not the person — evidence, the root cause and the scenario
     titles all come from production, so all of them are scrubbed. */
  let replaced = 0;
  const scrub = (raw) => {
    const clean = scrubPersonalData(raw, { names });
    replaced += clean.count;
    return clean.text;
  };
  const excerpts = evidence.slice(0, 5).map((e) => scrub(typeof e === "string" ? e : `${e.source ? `${e.source}: ` : ""}${e.text}`));
  const cause = rootCause ? scrub(rootCause) : null;
  const scenarios = failingScenarios.map(asText).filter(Boolean).map(scrub);
  const more = scenarios.length > 12 ? scenarios.length - 12 : 0;
  /* Where in the agent it goes wrong — what a change should start from. */
  const diagLines = diagnosis ? [
    "## Diagnosis",
    ...(diagnosis.where || []).map((w) => `- ${w.kind}: \`${w.ref}\`${w.quote ? ` — “${w.quote}”` : ""}`),
    "",
    `**Why it fails.** ${scrub(diagnosis.cause)}`,
    "",
    `**What to change.** ${scrub(diagnosis.change)}`,
    ...(diagnosis.edited ? ["", "_Edited by the person who started the fix._"] : []),
    "",
  ] : [];
  const cell = (x) => String(x).replace(/\|/g, "\\|");
  const rows = breakdown.slice(0, 20);
  const showRun = rows.some((x) => x.result);
  const breakdownLines = rows.length ? [
    "## Per-scenario breakdown",
    showRun ? `| Scenario | Today | ${rows.find((x) => x.run)?.run || "Last run"} |` : "| Scenario | Today |",
    showRun ? "| --- | --- | --- |" : "| --- | --- |",
    ...rows.map((x) => (showRun ? `| ${cell(scrub(x.title))} | ${x.today} | ${x.result || "—"} |` : `| ${cell(scrub(x.title))} | ${x.today} |`)),
    ...(breakdown.length > rows.length ? [`_…and ${breakdown.length - rows.length} more._`] : []),
    "",
    "_Held-back scenarios are left out: they prove the change, so they're never shown to whoever writes it._",
    "",
  ] : [];

  return [
    `# ${title}`,
    "",
    [
      agent?.name && `Agent: ${agent.name} (\`${agentKey}\`)`,
      env?.name && `Environment: ${env.name} (\`${envId}\`)`,
      severity && `Severity: ${severity}`,
      issue?.id && `Issue: ${issue.id}`,
      runId && `Run: ${runId}`,
      /* "agent v3 × env v7" — the environment version stays fixed; only the agent changes. */
      pairing && `Measured on: ${pairing}`,
      /* Every proof names where the candidate ran — the line every surface shows. */
      proof && `Proof: ${proof.line || `${proof.label} · ${proof.tierLabel}${proof.kind === "live" ? " — tools reached your real systems" : ""}`}`,
      members?.length && `Changes: ${members.join(", ")}`,
    ].filter(Boolean).join("  \n"),
    "",
    ...diagLines,
    diagnosis ? "## Symptom" : "## Root cause",
    cause || "Not analysed yet.",
    "",
    /* With a diagnosis, its What to change is the suggestion — not said twice. */
    ...(diagnosis && !suggestedFix ? [] : ["## Suggested fix", ...fixLines, ""]),
    ...(excerpts.length ? [
      "## Evidence",
      `_Personal data replaced — names, phone numbers, emails, addresses and account numbers are realistic fake values${replaced ? ` (${replaced} replaced)` : ""}. The problem is kept, not the person._`,
      "",
      ...excerpts.map((e) => `> ${e}`).flatMap((e) => [e, ""]),
    ] : []),
    ...breakdownLines,
    ...(scenarios.length && !breakdownLines.length ? [
      "## Failing scenarios",
      ...bullets(scenarios.slice(0, 12)),
      ...(more ? [`- …and ${more} more`] : []),
      "",
    ] : []),
    "## Constraints",
    ...bullets(limitRows(limits, model).map((r) => `${r.label}: ${r.value}`)),
    "",
    "## Submit a candidate",
    /* What to submit depends on where the agent lives — a commit, a settings
       patch, a draft version (candidateRoute). */
    submit || submitStepOf(agent, envId),
    "",
    "You can propose changes but cannot change the scenarios or the grading.",
    "",
  ].join("\n");
};
