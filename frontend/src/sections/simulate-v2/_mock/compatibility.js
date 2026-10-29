/*
  Compatibility and the release bar (roadmap release.md, Week 1): whether
  this agent can run this world — per capability, never one green badge —
  and exactly which cases each gap affects. Supported, Partial, Blocked,
  Unavailable, Untested, Stale or Deprecated, each with what we found and
  the repair. Blocked cases stay in the requested count: running only the
  eligible ones is an explicit, reviewed choice, and the run says
  "6 evaluated / 10 requested", not 100%.

  Built from what's already recorded: the agent's connection check, how it
  is connected (code, endpoint, platform, built here), and the world (seeded
  or connect-only, its channel).
*/
import { checksConnection, connectionSpecOf, startsFromCode } from "./connectionCheck";
import { sourceKindOf } from "./agentRegistry";
import { takesSnapshot } from "./candidateSource";
import { proofKindOf } from "./rsiModel";
import { membersOf } from "./improvementPolicy";
import { isConnectOnly } from "./envModel";
import { knownToolsFor, requiredToolsOf } from "./toolFit";
import { currentAgentVersion, currentEnvVersion } from "./versions";
import { hashSeed } from "./runStream";

export const COMPAT_STATUS = {
  supported: { label: "Supported", dot: "passed", rank: 0 },
  partial: { label: "Partial", dot: "flaky", rank: 2 },
  blocked: { label: "Blocked", dot: "failed", rank: 5 },
  unavailable: { label: "Unavailable", dot: "cancelled", rank: 1 },
  untested: { label: "Untested", dot: "queued", rank: 3 },
  stale: { label: "Stale", dot: "needs_input", rank: 4 },
  deprecated: { label: "Deprecated", dot: "needs_input", rank: 2 },
};

export const CAPABILITIES = [
  { id: "start", label: "Start and health" },
  { id: "message", label: "One-turn messages" },
  { id: "continuity", label: "Multi-turn continuity" },
  { id: "streaming", label: "Streaming" },
  { id: "files", label: "Files and artifacts" },
  { id: "toolSchema", label: "Tool discovery" },
  { id: "toolRouting", label: "Controlled tool routing" },
  { id: "state", label: "State before and after" },
  { id: "audio", label: "Audio and timing" },
  { id: "browser", label: "Browser interaction" },
  { id: "children", label: "Child agents and skills" },
  { id: "cancel", label: "Cancel" },
  { id: "reset", label: "World reset" },
  { id: "resume", label: "Resume" },
  { id: "cost", label: "Cost measurement" },
];

const FROM_CHECK = { ok: "supported", failed: "blocked", unavailable: "unavailable" };
const VOICE = ["voice", "multi"];

/** The cases a full run of this world takes — or the ones asked for. */
const requestedOf = (envState, scenarioIds) => {
  const live = (envState?.scenarios || []).filter((s) => !s.archived && !s.retired && !s.proposed && !s.canary);
  if (!scenarioIds?.length) return live;
  const want = new Set(scenarioIds);
  return live.filter((s) => want.has(s.id));
};

/**
 * The report for `agent` on `env`: { identity, rows, notNeeded, scope,
 * worst, testedAt, cli }. Each row: { id, label, status, detail, repair?,
 * affected: [scenario ids], blocks: bool }.
 */
export const compatibilityOf = ({ env, envState, agent = envState?.agent, scenarioIds = null }) => {
  const v = agent?.values || {};
  const kind = sourceKindOf(agent);
  const checked = checksConnection(agent);
  const code = takesSnapshot(agent);
  const test = v.connectionTest || null;
  const results = Object.fromEntries((test?.results || []).map((r) => [r.id, r]));
  const surface = env?.surface || "chat";
  const voice = VOICE.includes(surface);
  const connectOnly = isConnectOnly(env);
  const live = proofKindOf(agent).kind === "live";
  const provider = v.provider ? v.provider.charAt(0).toUpperCase() + v.provider.slice(1) : "the platform";
  const version = currentAgentVersion(envState);
  const requested = requestedOf(envState, scenarioIds);
  const all = requested.map((s) => s.id);
  const known = knownToolsFor(env, envState);
  const needs = (tool) => requested.filter((s) => requiredToolsOf(s, known).includes(tool)).map((s) => s.id);
  const writeCases = requested.filter((s) => requiredToolsOf(s, known).some((t) => /refund|charge|pay|cancel|book|transfer|create|update|delete|issue|rebook/i.test(t))).map((s) => s.id);

  /* Stale: evidence that no longer matches — a credential changed, or the
     source moved on after the last test. */
  const testedAt = test?.at ? Date.parse(test.at) : null;
  const versionAt = Date.parse(version?.createdAt || version?.at || "") || null;
  const staleWhy = v.disconnected
    ? "A credential it uses changed since the last test"
    : testedAt && versionAt && versionAt > testedAt
      ? `Agent ${version.label} arrived after the last test`
      : null;

  const rows = [];
  const add = (id, status, detail, extra = {}) =>
    rows.push({ id, label: CAPABILITIES.find((c) => c.id === id).label, status, detail, affected: [], ...extra });
  /* A row from the connection check; Untested until a check ran; Stale when the check is out of date. */
  const fromCheck = (id, checkId, affected, fallback) => {
    if (!checked) return add(id, "supported", fallback, { affected: [] });
    const r = results[checkId];
    if (!r) return add(id, "untested", "No connection check has covered this yet — run it before trusting a run.", { affected, repair: "test" });
    if (staleWhy && r.state === "ok") return add(id, "stale", `${staleWhy}. Last result: ${r.detail}`, { affected, repair: "test" });
    const status = FROM_CHECK[r.state] || "untested";
    return add(id, status, r.detail, { affected: status === "supported" ? [] : affected, ...(status === "blocked" && { repair: "connection", blocks: true }) });
  };

  /* Start and health — a provider being retired is Deprecated, not broken. */
  if (v.provider === "bland")
    add("start", "deprecated", "Reachable, but Bland's older pathway agents are being retired on 15 Nov 2026 — move to its new agent model. Results so far stay readable.", { repair: "connection" });
  else fromCheck("start", "starts", all, kind === "platform" ? `Reached through ${provider}'s API.` : "Runs here — nothing to start.");
  fromCheck("message", "replies", all, kind === "platform" ? `${provider} answers every turn with a mapped reply.` : "Replies are mapped.");
  fromCheck("continuity", "continuity", all, "The same session keeps the conversation across turns.");

  const spec = checked ? connectionSpecOf(agent) : null;
  if (spec?.method === "sse" || spec?.method === "ws" || voice) add("streaming", "supported", voice ? "Audio and partial transcripts stream and end with a completion event." : "Partial replies arrive in order and end with a completion event.");
  else add("streaming", "unavailable", `Its connection replies whole over ${spec ? (spec.method || "http").toUpperCase() : "the API"} — no case here needs streaming.`);

  if (spec?.method === "async") fromCheck("files", "artifact", all, "");
  else add("files", "unavailable", "Not exposed by this connection — no case here grades a file.");

  const agentTools = known.length;
  if (kind === "builtin" || kind === "platform" || code) add("toolSchema", "supported", `${agentTools} tools with their arguments and results are known.`);
  else add("toolSchema", "partial", `${agentTools} tools are known from the world; the endpoint doesn't list its own — calls are matched by name and arguments.`);

  /* Controlled tool routing — the one that most often blocks cases. */
  const prod = test?.productionTools || [];
  if (checked && prod.length)
    add("toolRouting", "blocked", `${prod.join(", ")} still reaches production — its URL is hard-coded. Cases that need it can't run against the test world.`, { affected: [...new Set(prod.flatMap(needs))], repair: "connection", blocks: true });
  else if (checked) fromCheck("toolRouting", "tools", writeCases, "");
  else if (live)
    add("toolRouting", "partial", `Tools run in your ${provider} account. Reads are checked against the world; writes are observed, not controlled.`, { affected: writeCases });
  else add("toolRouting", "supported", "Every tool call is answered by the test world.");

  if (connectOnly) add("state", "unavailable", "Your agent calls its own systems, so before-and-after state can't be read here. Graded on the conversation and tool calls.", { affected: writeCases });
  else if (live) add("state", "partial", "The world's records are checked; anything written in your account isn't visible.", { affected: writeCases });
  else add("state", "supported", "Before and after state of the seeded world is captured each trial.");

  const notNeeded = [];
  if (voice) {
    if (kind === "platform" || /livekit|pipecat/i.test(v.framework || ""))
      add("audio", "supported", "Real audio with timing — latency and barge-in are measured.");
    else add("audio", "partial", "Text rehearsal only — dialogue and tools are checked; acoustic latency and barge-in stay unavailable, not zero.", { affected: all });
  } else notNeeded.push("Audio and timing");
  if (surface === "browser") add("browser", "untested", "Browser execution isn't certified yet — results here are untested, not supported.", { affected: all });
  else notNeeded.push("Browser interaction");
  const members = membersOf(agent);
  if (members.length > 1)
    add("children", "partial", `The coordinator and ${members.length - 1} workers report delegation and return. Skill activation is unknown without runtime evidence.`);
  else notNeeded.push("Child agents and skills");

  if (checked) fromCheck("cancel", "cancel", [], "");
  else add("cancel", kind === "platform" ? "supported" : "supported", kind === "platform" ? "Ending the call stops it, confirmed by the platform." : "Stopping ends the turn here.");
  if (connectOnly) add("reset", "unavailable", "A live system isn't assumed resettable — every attempt starts from whatever state it's in.", { affected: all });
  else add("reset", "supported", "The world returns to its seed between attempts.");
  add("resume", "partial", "Unfinished trials restart from their seed — the agent's memory isn't restored.");
  if (code || kind === "endpoint") add("cost", "partial", "Our credits are measured; your model's spend on your own key is unknown — shown as unknown, not zero.");
  else if (kind === "platform") add("cost", "supported", `${provider}'s per-minute cost is read from its API.`);
  else add("cost", "supported", "Every call is metered here.");

  /* Rows in the order the roadmap lists them. */
  rows.sort((a, b) => CAPABILITIES.findIndex((c) => c.id === a.id) - CAPABILITIES.findIndex((c) => c.id === b.id));

  const blockedIds = new Set(rows.filter((r) => r.blocks).flatMap((r) => r.affected));
  const blocked = requested.filter((s) => blockedIds.has(s.id)).map((s) => ({
    id: s.id,
    title: s.title || s.name || s.id,
    by: rows.filter((r) => r.blocks && r.affected.includes(s.id)).map((r) => r.label),
  }));
  const worst = rows.reduce((w, r) => (COMPAT_STATUS[r.status].rank > COMPAT_STATUS[w].rank ? r.status : w), "supported");
  const connectionRev = checked ? `conn-${(hashSeed(JSON.stringify(spec)) >>> 0).toString(16).slice(0, 6)}` : null;

  return {
    identity: {
      agent: agent?.name || "Agent",
      agentVersion: version?.label || "v1",
      connection: connectionRev,
      world: env?.name,
      worldVersion: currentEnvVersion(env, envState)?.label || "v1",
      release: env?.release || null,
      mode: [envState?.agent?.values?.runLocation === "local" ? "On your machine" : "Hosted", live ? "Live" : "Sealed"].join(" · "),
    },
    rows,
    notNeeded,
    scope: {
      requested: requested.length,
      eligible: requested.length - blocked.length,
      blocked,
      eligibleIds: requested.filter((s) => !blockedIds.has(s.id)).map((s) => s.id),
    },
    counts: Object.fromEntries(Object.keys(COMPAT_STATUS).map((k) => [k, rows.filter((r) => r.status === k).length])),
    worst,
    testedAt: test?.at || null,
    staleWhy,
    fromCode: startsFromCode(agent),
  };
};

/** "12 supported · 1 blocked · 2 untested" — the compact summary. */
export const compatSummaryLine = (report) =>
  Object.entries(report.counts)
    .filter(([, n]) => n)
    .sort(([a], [b]) => COMPAT_STATUS[b].rank - COMPAT_STATUS[a].rank)
    .map(([k, n]) => `${n} ${COMPAT_STATUS[k].label.toLowerCase()}`)
    .join(" · ");

/** What a run stamps, so the report stays with it. */
export const compatStampOf = (report, evaluatedIds) => ({
  at: new Date().toISOString(),
  identity: report.identity,
  requested: report.scope.requested,
  evaluated: evaluatedIds.length,
  blocked: report.scope.blocked,
  rows: report.rows.map((r) => ({ id: r.id, label: r.label, status: r.status })),
});

export const doctorCli = (agent, env) => `agent-learn doctor --agent "${agent?.name || "agent"}" --world ${env?.id}`;
