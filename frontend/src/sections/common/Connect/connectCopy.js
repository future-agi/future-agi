import { formatExactMoney } from "src/sections/common/format/money";
// Copy of the live connection (R4 implementation; exact strings).
// Pure. Every number in a sentence comes from the record it describes
// (C10): templates carry no digit literals except the rule constants below.
import { plural } from "src/sections/common/vocab";
import { fmtDuration } from "src/sections/common/Media/mediaRef";
import { fmtClock, firstName } from "./connectModel";
import {
  PERMISSION_LABELS,
  RECORD_WORDS,
  recordKeyOf,
} from "src/sections/common/RunSources/recipe/recipeModel";

/** `Run with 8 slots` doubles the machine's concurrency. */
export const BUSY_SLOT_FACTOR = 2;
const SHA_LEN = 7;

const join = (parts) => parts.filter(Boolean).join(" · ");
const fileOf = (entry) => String(entry ?? "").split(":")[0] || "your code";
const listWords = (words) => {
  const w = (words ?? []).filter(Boolean);
  if (w.length <= 1) return w.join("");
  return `${w.slice(0, -1).join(", ")} and ${w[w.length - 1]}`;
};

/**
 * An age in whole units: `4 s`, `12 min`, `2 h`, `3 d` (heartbeats,
 * `No heartbeat for 2 h`, `agent.py changed 4 s ago`).
 */
export function fmtAge(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return "0 s";
  const s = Math.floor(n / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h`;
  return `${Math.floor(h / 24)} d`;
}

/** `Heartbeat 2 s ago`. */
export const heartbeatText = (ageMs) => `Heartbeat ${fmtAge(ageMs)} ago`;

/** `Waiting for your agent… 0:12`. */
export const waitingLine = (ms) => `Waiting for your agent… ${fmtClock(ms)}`;

export const WAITING_HELP =
  "Run the command above in your project folder. It connects out to Future AGI, so there are no ports to open.";

export const STUCK_HELP =
  'Nothing yet? The command prints "Connected" when it reaches Future AGI. Check that it is still running and that FI_API_KEY is set in that shell.';

/** `git 9f2c1a7 · 2 files changed` (clean: `git 9f2c1a7`). */
export function gitText(git) {
  if (!git?.commit) return "";
  const sha = String(git.commit).slice(0, SHA_LEN);
  const n = git.dirty ? git.files?.length ?? 0 : 0;
  return n ? `git ${sha} · ${plural(n, "file")} changed` : `git ${sha}`;
}

/** `LangGraph 0.4.8 · Python 3.12` (code) — the framework part of a handshake. */
export const runtimeText = (agent) =>
  join([agent?.framework_version, agent?.runtime]);

/**
 * The handshake line (R4-SPEC §4):
 * code    `Connected · maya@macbook-air · support_agent (LangGraph 0.4.8 · Python 3.12) · git 9f2c1a7 · 2 files changed`
 * harness `Connected · maya@macbook-air · Claude Code 2.0 headless · Docker 27.1 · records every message and tool call, as a trace · API key`
 * voice   `Connected · maya@macbook-air · LiveKit worker acme-voice-v2 · wss://acme.livekit.cloud · 5 at a time`
 */
export function handshakeLine(agent, machine) {
  if (!agent) return "";
  const host = machine?.name ?? "your machine";
  if (agent.kind === "harness") {
    // One wording with the recipe's What we record chip (RECORD_WORDS,
    // keyed by the harness the same way the chip is).
    const c = agent.capture ?? {};
    const key = agent.entry_id ? recordKeyOf(agent.entry_id) : null;
    const capture = [
      key && RECORD_WORDS[key]
        ? RECORD_WORDS[key]
        : c.steps === "transcript"
          ? RECORD_WORDS.transcript
          : c.steps === "final_only"
            ? "the final result only"
            : c.model_calls === "gateway"
              ? "model calls only"
              : null,
    ].filter(Boolean);
    return join([
      "Connected",
      host,
      agent.framework_version,
      agent.runtime,
      capture.length ? `records ${capture.join(" + ")}` : null,
      c.auth === "api_key"
        ? "API key"
        : c.auth === "subscription"
          ? "subscription login"
          : null,
    ]);
  }
  if (agent.kind === "voice") {
    const fw = String(agent.framework_version ?? "").replace(
      /\s+Agents.*$/,
      "",
    );
    return join([
      "Connected",
      host,
      `${fw || "Voice"} worker ${agent.name}`,
      agent.livekit_url ?? agent.endpoint_url,
      agent.concurrency ? `${agent.concurrency} at a time` : null,
    ]);
  }
  const rt = runtimeText(agent);
  return join([
    "Connected",
    host,
    rt ? `${agent.name} (${rt})` : agent.name,
    gitText(agent.git),
  ]);
}

/**
 * The part of the handshake the panel prints after the pill and the agent
 * name: code `LangGraph 0.4.8 · Python 3.12 · git 9f2c1a7 · 2 files
 * changed`, harness `Claude Code 2.0 headless · Docker 27.1 · capture: …
 * · API key`, voice `LiveKit Agents 1.2 · wss://acme.livekit.cloud · 5 at a
 * time`.
 */
export function handshakeDetail(agent) {
  if (!agent) return "";
  if (agent.kind === "harness")
    return handshakeLine(agent, { name: "-" })
      .split(" · ")
      .slice(2)
      .join(" · ");
  if (agent.kind === "voice")
    return join([
      agent.framework_version,
      agent.livekit_url ?? agent.endpoint_url,
      agent.concurrency ? `${agent.concurrency} at a time` : null,
    ]);
  return join([agent.framework_version, agent.runtime, gitText(agent.git)]);
}

/** `We read 4 parameters, 7 tools and 2 models from your agent.` */
export function readSummary(agent) {
  if (!agent) return "";
  const parts = [
    agent.params_schema?.length
      ? plural(agent.params_schema.length, "parameter")
      : null,
    agent.tools?.length ? plural(agent.tools.length, "tool") : null,
    agent.mcp_servers?.length
      ? `${plural(agent.mcp_servers.length, "MCP server")} (${plural(
          agent.mcp_servers.reduce((n, m) => n + (Number(m.tools) || 0), 0),
          "tool",
        )})`
      : null,
    agent.models?.length ? plural(agent.models.length, "model") : null,
  ];
  const what = listWords(parts);
  return what
    ? `We read ${what} from your agent.`
    : "Your agent declared no parameters, tools or models.";
}

/** `Simulated · first result 1 m 04 s after you opened this` (prototype) or without `Simulated`. */
export function firstResultLine(ms, { simulated = false } = {}) {
  const t = fmtDuration(ms);
  return simulated
    ? `Simulated · first result ${t} after you opened this`
    : `First result ${t} after you opened this`;
}

export const FIRST_RESULT_SIMULATED_HELP =
  "Timed from the scripted connection, not from your machine.";

/** `Only Ravi Menon can run on ravi@thinkpad. Teammates can run here when Ravi turns on Allow teammates.` */
export function notAllowedLine(machine) {
  const owner = machine?.owner?.name ?? "the owner";
  return `Only ${owner} can run on ${machine?.name ?? "this machine"}. Teammates can run here when ${firstName(owner)} turns on Allow teammates.`;
}

/** `alice@mbp-14 is offline · last seen 2 h ago.` */
export function offlineLine(machine, lastSeenAgoMs) {
  return `${machine?.name ?? "This machine"} is offline · last seen ${fmtAge(lastSeenAgoMs)} ago.`;
}

// ----------------------------------------------------------- access panel

/** `Accepts from the UI: model, system_prompt, temperature, retriever_k (declared in agent.py). Anything else is refused by the worker.` */
export function acceptsLine(agent) {
  const names = (agent?.params_schema ?? []).map((p) => p.name);
  if (!names.length)
    return `Accepts nothing from the UI: ${agent?.name ?? "this agent"} declares no parameters, so runs use the code as it is.`;
  const where =
    agent?.kind === "harness"
      ? "declared by the harness worker"
      : `declared in ${fileOf(agent?.entry)}`;
  return `Accepts from the UI: ${names.join(", ")} (${where}). Anything else is refused by the worker.`;
}

/** The recipe-change rule of a machine. */
export function recipeLine(machine) {
  return machine?.allow_recipe_changes
    ? "Recipe changes that widen permissions (Full auto, a new MCP server, a custom command): allowed: started with --allow-recipe-changes"
    : "Recipe changes that widen permissions (Full auto, a new MCP server, a custom command): need a confirmation on this machine";
}

/** `Calls real systems: issue_refund, offer_replacement`. */
export function sideEffectsLine(agent) {
  const t = agent?.side_effect_tools ?? [];
  return t.length
    ? `Calls real systems: ${t.join(", ")}`
    : "No tools with side effects declared.";
}

// ----------------------------------------------------------- run strip

/**
 * options-truth L2 G3 (S5 tries vocabulary): what a live counter says,
 * from an activity body's run-read counts (`rows_*`, `tries_*`). One try:
 * `13/30 · 10 passed · 3 failed`; more: `13/30 tasks × 3 tries · 34/39
 * tries passed`, and once the run ends `(87.2%) · 2 flaky` (the % is the
 * grid's pass rate: passed tries ÷ finished tries). Null without counts.
 * @returns {{progress: string, counts: string[]}|null}
 */
export function countsTextOf(a, { final = false } = {}) {
  if (a?.rows_total == null) return null;
  const t = Math.max(1, Number(a.trials) || 1);
  const noun = a.row_noun === "task" ? "tasks" : "rows";
  const passed = a.tries_passed ?? 0;
  const failed = a.tries_failed ?? 0;
  const judged = passed + failed;
  const progress =
    t > 1
      ? `${a.rows_done ?? 0}/${a.rows_total} ${noun} × ${t} tries`
      : `${a.rows_done ?? 0}/${a.rows_total}`;
  if (!judged) return { progress, counts: [] };
  if (t === 1)
    return { progress, counts: [`${passed} passed`, `${failed} failed`] };
  const pct = a.pass_pct ?? Math.round((passed / judged) * 1000) / 10;
  return {
    progress,
    counts: final
      ? [
          `${passed}/${judged} tries passed (${Number(pct).toFixed(1)}%)`,
          a.flaky_rows ? `${a.flaky_rows} flaky` : null,
        ].filter(Boolean)
      : [`${passed}/${judged} tries passed`],
  };
}

/** `Stopped at cost cap $0.90` (the run's own reason) or null. */
function stopReasonOf(a) {
  if (!a?.stop_reason) return null;
  return a.cap_usd != null && /cost cap/i.test(a.stop_reason)
    ? `Stopped at cost cap ${formatExactMoney(a.cap_usd)}`
    : a.stop_reason;
}

/** `· 27 carried` on a rerun that carried its parent's rows. */
const carriedOf = (a) => (a?.carried_rows ? [`${a.carried_rows} carried`] : []);

/** `Running on alice@mbp-14 · 4 at a time · 13/30 tasks × 3 tries · 34/39 tries passed`. */
export function stripLine(a) {
  if (!a) return "";
  if (a.terminal) return terminalStripLine(a);
  const host = a.machine?.name ?? "your machine";
  const c = countsTextOf(a, { final: ["done", "stopped"].includes(a.state) });
  if (c) {
    const left = a.rows_left
      ? [`${a.rows_left} ${a.row_noun === "task" ? "tasks" : "rows"} left`]
      : [];
    if (a.state === "stopped")
      return join([
        `${stopReasonOf(a) ?? "Stopped"} on ${host}`,
        c.progress,
        ...c.counts,
        ...left,
      ]);
    if (a.state === "done")
      return join([
        `Finished ${c.progress}`,
        ...c.counts,
        ...carriedOf(a),
        `ran on ${host}`,
      ]);
    if (a.state === "scoring")
      return join([`Scoring on ${host}`, c.progress, ...c.counts]);
    if (a.state === "waiting") return join([`Waiting for ${host}`, c.progress]);
    return join([
      `Running on ${host}`,
      a.slots ? `${a.slots} at a time` : null,
      c.progress,
      ...c.counts,
      ...carriedOf(a),
    ]);
  }
  const progress = `${a.done ?? 0}/${a.total ?? 0}`;
  const failed = a.failed ? `${a.failed} failed` : null;
  if (a.state === "stopped")
    return join([`Stopped on ${host}`, progress, failed]);
  if (a.state === "done")
    return join([`Finished on ${host}`, progress, failed]);
  return join([
    `Running on ${host}`,
    a.slots ? `${a.slots} at a time` : null,
    progress,
    failed,
  ]);
}

/**
 * IT-R1 G1 P5: a run started from the user's terminal reads the run's own
 * status (`activity.terminal`): `Waiting for your terminal · 0/24` ·
 * `Running on your machine · 9/24 · 5 passed · 4 failed` · `Scoring 24/24`
 * · `Finished 24/24 · 13 passed · 11 failed · ran on your machine`.
 * options-truth L2 G3: rows for progress, tries for the counts, never
 * capped at the row count (`Finished 30/30 tasks × 2 tries · 47/60 tries
 * passed (78.3%) · 9 flaky · ran on your machine`).
 */
export function terminalStripLine(a) {
  const c = countsTextOf(a, { final: ["done", "stopped"].includes(a.state) });
  if (c) {
    if (a.state === "done")
      return join([
        `Finished ${c.progress}`,
        ...c.counts,
        ...carriedOf(a),
        "ran on your machine",
      ]);
    if (a.state === "scoring")
      return join([`Scoring ${c.progress}`, ...c.counts]);
    if (a.state === "stopped")
      return join([
        stopReasonOf(a) ?? "Stopped",
        c.progress,
        ...c.counts,
        ...(a.rows_left
          ? [`${a.rows_left} ${a.row_noun === "task" ? "tasks" : "rows"} left`]
          : []),
      ]);
    if (a.state === "running")
      return join(["Running on your machine", c.progress, ...c.counts]);
    return join(["Waiting for your terminal", c.progress, ...c.counts]);
  }
  const done = a.done ?? 0;
  const total = a.total ?? 0;
  const progress = `${done}/${total}`;
  // IT-R5 G1 P2 (one clock): counts are scored rows of the run's own
  // progress (`Scoring 3/24 · 2 passed · 1 failed`), never more than the
  // rows that arrived.
  const scored = Math.min(done, a.scored ?? done);
  const passed = Math.min(scored, a.passed ?? 0);
  const failed = Math.min(scored - passed, a.failed ?? 0);
  const counts = scored > 0 ? [`${passed} passed`, `${failed} failed`] : [];
  if (a.state === "done")
    return join([`Finished ${progress}`, ...counts, "ran on your machine"]);
  if (a.state === "scoring")
    return join([`Scoring ${scored}/${total}`, ...counts]);
  if (a.state === "stopped") return join([`Stopped ${progress}`, ...counts]);
  if (a.state === "running")
    return join([
      "Running on your machine",
      progress,
      ...(scored < done ? [`scored ${scored}`] : []),
      ...counts,
    ]);
  if (done > 0) return join(["Waiting for your terminal", progress, ...counts]);
  return join(["Waiting for your terminal", progress]);
}

/** The Stop confirm: `Stop #3G on alice@mbp-14?` + `4 tries in progress finish as Stopped. 49 tries won't start.` */
export function stopConfirmOf(a) {
  const host = a?.machine?.name ?? "your machine";
  const running = a?.running ?? 0;
  const left = a?.left ?? Math.max(0, (a?.total ?? 0) - (a?.done ?? 0));
  return {
    title: `Stop ${a?.run_label ?? "this run"} on ${host}?`,
    body: `${plural(running, "try", "tries")} in progress finish as Stopped. ${plural(left, "try", "tries")} won't start.`,
  };
}

/** `Resume · 49 left`. */
export const resumeLabel = (a) =>
  a?.rows_left != null
    ? `Resume · ${a.rows_left} ${a.row_noun === "task" ? "tasks" : "rows"} left`
    : `Resume · ${a?.left ?? Math.max(0, (a?.total ?? 0) - (a?.done ?? 0))} left`;

/**
 * `agent.py changed 4 s ago · this run keeps 9f2c1a7 · Rerun the 5 failed
 * tasks with the new code`: the rerun repeats every try of a failed task
 * (R2 iterate, scope `failed`), so the line counts tasks, not tries.
 */
export function reloadLine(a, reload, now) {
  const r = reload ?? a?.reloads?.[a.reloads.length - 1];
  if (!r) return "";
  const file = r.files?.[0]?.path ?? "Your code";
  const age = fmtAge(Math.max(0, Number(now) - Date.parse(r.at)));
  const rows = a?.failed_rows ?? 0;
  const noun = a?.row_noun === "task" ? "task" : "row";
  return join([
    `${file} changed ${age} ago`,
    `this run keeps ${String(a?.sha ?? "").slice(0, SHA_LEN)}`,
    rows
      ? `Rerun the ${plural(rows, `failed ${noun}`)} with the new code`
      : "rerun with the new code",
  ]);
}

/** `Stopped #3G on alice@mbp-14 · 4 tries marked Stopped`. */
export function stoppedToast(a, res) {
  const n =
    (res?.lines ?? []).filter((l) => l.stopped).length || a?.running || 0;
  return join([
    `Stopped ${a?.run_label ?? "the run"} on ${a?.machine?.name ?? "the machine"}`,
    n ? `${plural(n, "try", "tries")} marked Stopped` : null,
  ]);
}

/** `Started #3H with a41c2e0 · 6 tries rerun, 84 carried`. */
export function rerunToast(res) {
  return `Started ${res?.run_label ?? "a new run"} with ${String(res?.sha ?? "").slice(0, SHA_LEN)} · ${plural(res?.rerun ?? 0, "try", "tries")} rerun, ${res?.carried ?? 0} carried`;
}

/** `Waiting for Alice Park to confirm on alice@mbp-14 · Full auto widens permissions`. */
export function localConfirmLine(a) {
  const pc = a?.pending_confirm ?? {};
  return `Waiting for ${pc.owner ?? a?.machine?.owner?.name ?? "the owner"} to confirm on ${a?.machine?.name ?? "the machine"} · ${pc.reason ?? "Full auto widens permissions"}`;
}

/** `T3 · try 1 · passed · 9.2 s · 9f2c1a7 · trace 7f2c…`. */
export function tryLineText(l, { fmtMs } = {}) {
  const outcome =
    l.passed === true
      ? "passed"
      : l.passed === false
        ? "failed"
        : l.stopped
          ? "Stopped by you"
          : l.scoring
            ? "scoring"
            : "running";
  return join([
    l.row_label,
    `try ${l.trial}`,
    outcome,
    l.duration_ms != null && fmtMs ? fmtMs(l.duration_ms) : null,
    l.code_sha ? String(l.code_sha).slice(0, SHA_LEN) : null,
    l.trace_id ? `trace ${String(l.trace_id).slice(0, 4)}…` : null,
  ]);
}

/** `Running T1 · trial 1…`. */
export const tryRunningLine = (rowLabel, trial = 1) =>
  `Running ${rowLabel} · trial ${trial}…`;

// ----------------------------------------------------------- pill

/**
 * What a ConnectionPill says (R4-SPEC D21): `alice@mbp-14 · Alice Park ·
 * online`, your own `maya@macbook-air · you · online`, `ci-runner-7 · Workspace
 * runner · last seen …`, `ravi@thinkpad · only Ravi Menon` (locked wins),
 * `ravi@thinkpad · Ravi Menon · update needed`, `alice@mbp-14 · offline`.
 * `lastSeen: true` asks the pill to render `last seen <RelativeTime>`.
 * @returns {{tone: "online"|"stale"|"update"|"locked"|"offline"|"unknown",
 *   name: string, owner: string|null, status: string|null, lastSeen: boolean}}
 */
export function pillOf(
  machine,
  { showOwner = true, showLastSeen = false } = {},
) {
  if (!machine)
    return {
      tone: "unknown",
      name: "Not connected",
      owner: null,
      status: null,
      lastSeen: false,
    };
  const you = Boolean(machine.you);
  const ownerName = machine.workspace_runner
    ? "Workspace runner"
    : you
      ? "you"
      : machine.owner?.name ?? null;
  if (machine.may_use === false && !you)
    return {
      tone: "locked",
      name: machine.name,
      owner: null,
      status: `only ${machine.owner?.name ?? "the owner"}`,
      lastSeen: false,
    };
  const owner = showOwner || you ? ownerName : null;
  switch (machine.status) {
    case "online":
      return {
        tone: "online",
        name: machine.name,
        owner,
        status: "online",
        lastSeen: false,
      };
    case "stale":
      return {
        tone: "stale",
        name: machine.name,
        owner,
        status: "last seen",
        lastSeen: true,
      };
    case "offline":
      return {
        tone: "offline",
        name: machine.name,
        owner,
        status: showLastSeen ? "offline · last seen" : "offline",
        lastSeen: showLastSeen,
      };
    default:
      return {
        tone: "unknown",
        name: machine.name,
        owner,
        status: machine.status ?? null,
        lastSeen: false,
      };
  }
}

/**
 * The pill's tooltip lines (owner, OS, what the user may do). dataset integration FE-0:
 * no SDK version line (the versions it named do not exist).
 */
export function pillHelp(machine) {
  if (!machine) return ["This machine is not connected right now."];
  const lines = [];
  const owner = machine.you ? "You" : machine.owner?.name ?? "Unknown";
  lines.push(
    machine.workspace_runner
      ? "Workspace runner · anyone in the workspace can use it"
      : `Owner: ${owner}`,
  );
  if (machine.os) lines.push(machine.os);
  // options-truth L7b G4 (runners:page): Runners and sharing a machine are
  // absent, so the help never says a teammate may run here.
  if (machine.may_use === false && !machine.you)
    lines.push(notAllowedLine(machine));
  return lines;
}

/**
 * The ONE stand-in tag wording (L7c g1, the capability contract c): the `Simulated` tag
 * sits only next to a stand-in button, and its tooltip names what the button
 * stands in for: `Stands in for your terminal sending results.`
 */
export function standInTip(event) {
  const e = String(event ?? "").trim() || "the machine sending results";
  return `Stands in for ${e}.`;
}

/**
 * L7c g1 (copy:simulated-tag): the ONE name of each machine a stand-in
 * button stands in for (standInEvents.js, shared with runLedger). Every
 * `Simulate …` button and its tag take the event from there.
 */
export { STAND_IN_EVENTS } from "./standInEvents";

/** `<host> answering` (an endpoint test's stand-in). */
export const hostAnsweringEvent = (host) =>
  `${String(host ?? "").trim() || "the endpoint"} answering`;

/** `starting <server> and listing its tools` (an MCP server's stand-in). */
export const mcpListingEvent = (server) =>
  `starting ${String(server ?? "").trim() || "the MCP server"} and listing its tools`;

/** `<owner> confirming on <machine>` (a held rerun's stand-in). */
export const ownerConfirmEvent = (owner, machine) =>
  `${owner || "the owner"} confirming on ${machine || "the machine"}`;

/** A stand-in status line: `Simulated · Stands in for <event>.` */
export const standInLine = (simulatedWord, event) =>
  `${simulatedWord} · ${standInTip(event)}`;

/**
 * Deprecated (L7c g1, the capability contract c): a running progress line carries no
 * `Simulated` playback tag any more; kept only until RunConnectionStrip (g5)
 * drops its last call.
 */
export function playbackTip(speed) {
  const x = Number(speed);
  return x > 1
    ? `Plays back ${Math.round(x)}× faster than real time`
    : "Plays back at the pace the run would really take";
}

/**
 * The drawer's live counters (TodayStatus): the checklist line and the
 * finished footer, from an activity body's counts (S5). `Tries 39 of 90 ·
 * 34 passed · 5 failed · 13/30 tasks` / `Tasks 13 of 30 · 10 passed · 3
 * failed`; `Results in · 90 of 90 tries · 84 passed · 6 failed`. Pure.
 */
export function liveCountsOf(a, { rowNoun = "row", rowCount, tries } = {}) {
  const t = Math.max(1, Number(a?.trials ?? tries) || 1);
  const Noun = rowNoun === "task" ? "Tasks" : "Rows";
  const noun = rowNoun === "task" ? "tasks" : "rows";
  const rowsTotal = a?.rows_total ?? rowCount ?? 0;
  const rowsDone = a?.rows_done ?? 0;
  const triesTotal = a?.tries_total ?? rowsTotal * t;
  const triesDone = a?.tries_done ?? 0;
  const passed = a?.tries_passed ?? 0;
  const failed = a?.tries_failed ?? 0;
  const judged = passed + failed;
  const counts = judged ? [`${passed} passed`, `${failed} failed`] : [];
  const checklist =
    t > 1
      ? join([
          `Tries ${triesDone} of ${triesTotal}`,
          ...counts,
          `${rowsDone}/${rowsTotal} ${noun}`,
        ])
      : join([`${Noun} ${rowsDone} of ${rowsTotal}`, ...counts]);
  const stop = stopReasonOf(a);
  const footer = stop
    ? join([
        stop,
        `${rowsDone}/${rowsTotal}${t > 1 ? ` ${noun} × ${t} tries` : ""}`,
        ...counts,
        a?.rows_left ? `${a.rows_left} ${noun} left` : null,
      ])
    : join([
        "Results in",
        t > 1
          ? `${triesDone} of ${triesTotal} tries`
          : `${rowsDone} of ${rowsTotal} ${noun}`,
        ...counts,
      ]);
  return { checklist, footer, trials: t };
}

/**
 * L3 G5 (L4): the recipe a harness Try ran with, as its card echoes it:
 * `Ran with gpt-5 · Max turns 12 · Read only · MCP acme-support`. Empty
 * when the try ran without a recipe. Pure.
 */
export function recipeEchoLine(recipe) {
  if (!recipe) return "";
  const servers = (recipe.mcp_servers ?? []).filter(Boolean);
  const parts = [
    recipe.model,
    recipe.max_turns != null ? `Max turns ${recipe.max_turns}` : null,
    recipe.permissions
      ? PERMISSION_LABELS[recipe.permissions] ?? recipe.permissions
      : null,
    servers.length ? `MCP ${servers.join(", ")}` : null,
  ].filter(Boolean);
  return parts.length ? `Ran with ${parts.join(" · ")}` : "";
}

/**
 * L3 G5 (L2): what the changed knobs did on a Try, its own line under the
 * reply (`Read 12 records (Retriever k) · Answered with gpt-4o`). Pure.
 */
export function tryEffectsLine(effects) {
  const list = (effects ?? []).filter(Boolean);
  return list.length ? `What your settings did: ${list.join(" · ")}` : "";
}
