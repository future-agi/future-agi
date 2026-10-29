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
import { agentToolsFor, knownToolsFor, requiredToolsOf } from "./toolFit";
import { currentAgentVersion, currentEnvVersion } from "./versions";
import { hashSeed } from "./runStream";
import { familyOf } from "./testedVersions";

const agentVersionWord = (v) => (v?.label ? `${v.label} ` : "");

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
  /* The gap people hit first: a case needs a tool (an MCP, a file write)
     the agent doesn't have — those cases can't pass, so they're blocked. */
  { id: "toolsNeeded", label: "Tools the cases need" },
  { id: "toolRouting", label: "Controlled tool routing" },
  { id: "state", label: "State before and after" },
  { id: "audio", label: "Audio and timing" },
  { id: "browser", label: "Browser interaction" },
  { id: "children", label: "Child agents and skills" },
  { id: "pinning", label: "Version pinning" },
  { id: "transfer", label: "Transfers" },
  { id: "trace", label: "Trace visibility" },
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
  /* The tested family it belongs to — context, never proof for this connection. */
  const family = familyOf(agent, kind);
  const requested = requestedOf(envState, scenarioIds);
  const all = requested.map((s) => s.id);
  const known = knownToolsFor(env, envState);
  const needs = (tool) => requested.filter((s) => requiredToolsOf(s, known).includes(tool)).map((s) => s.id);
  const writeCases = requested.filter((s) => requiredToolsOf(s, known).some((t) => /refund|charge|pay|cancel|book|transfer|create|update|delete|issue|rebook/i.test(t))).map((s) => s.id);

  /* Stale: evidence that no longer matches — a credential changed, or the
     source moved on after the last test. */
  const testedAt = test?.at ? Date.parse(test.at) : null;
  /* Only a version that was actually saved — a stand-in "v1" is dated now. */
  const saved = (envState?.agentVersions || []).find((x) => x.label === version?.label);
  const versionAt = Date.parse(saved?.createdAt || saved?.at || "") || null;
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

  /* Tools the cases need that this agent version doesn't have. */
  const agentHas = new Set(agentToolsFor(env, envState, version?.label).map((t) => t.name));
  const missing = {};
  requested.forEach((s) =>
    requiredToolsOf(s, known)
      .filter((t) => !agentHas.has(t))
      .forEach((t) => {
        (missing[t] = missing[t] || []).push(s.id);
      }),
  );
  const missingNames = Object.keys(missing);
  if (!agentHas.size)
    add("toolsNeeded", "untested", "This agent's tools aren't known yet — sync it or run the connection check, then we can say which cases it can't do.", { affected: writeCases, repair: "test" });
  else if (missingNames.length)
    add(
      "toolsNeeded",
      "blocked",
      `${agent?.name || "This agent"} ${agentVersionWord(version)}has no ${missingNames.slice(0, 3).join(", ")}${missingNames.length > 3 ? ` or ${missingNames.length - 3} more` : ""} — ${missingNames.length === 1 ? "it's" : "they're"} needed by ${new Set(Object.values(missing).flat()).size} case${new Set(Object.values(missing).flat()).size === 1 ? "" : "s"}, which would fail for a missing tool, not the agent's judgment. Add ${missingNames.length === 1 ? "it" : "them"} to the agent, or run the rest.`,
      { affected: [...new Set(Object.values(missing).flat())], repair: "agent", blocks: true },
    );
  else {
    const used = new Set(requested.flatMap((s) => requiredToolsOf(s, known))).size;
    add("toolsNeeded", "supported", `The agent has every tool the requested cases call${used ? ` — all ${used}` : ""}.`);
  }

  /* Controlled tool routing — the one that most often blocks cases. */
  const prod = test?.productionTools || [];
  if (checked && prod.length)
    add("toolRouting", "blocked", `${prod.join(", ")} still reaches production — its URL is hard-coded. Cases that need it can't run against the test world.`, { affected: [...new Set(prod.flatMap(needs))], repair: "connection", blocks: true });
  else if (checked) fromCheck("toolRouting", "tools", writeCases, "");
  else if (live)
    add("toolRouting", "partial", `Tools run in your ${provider} account. Reads are checked against the world; writes are observed, not controlled.${family?.limits && family.limits !== "—" ? ` ${family.limits}.` : ""}`, { affected: writeCases });
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

  /* Platform agents: what the provider lets us pin, transfer and see. */
  if (kind === "platform") {
    if (v.providerPinned) add("pinning", "supported", `Runs are pinned to ${provider} ${v.providerVersion}.`);
    else if (v.providerVersion) add("pinning", "partial", `${provider} ${v.providerVersion} is a draft — it can change between runs, so results are marked unpinned.`);
    else add("pinning", "unavailable", `${provider} doesn't version this agent. ${version?.label || "v1"} is our snapshot of its config — if it changes on ${provider}, runs are marked unpinned until you sync.`);
    if (voice) {
      const t = v.platformTest;
      if (t?.transfer === "tested") add("transfer", "supported", `A phone test reached the transfer — it rang the test destination ${t.destination || ""} and connected.`.replace(/ +/g, " "));
      else add("transfer", "untested", "A text or web test doesn't prove a phone transfer — it stays untested until a phone test reaches one.");
    }
    add("trace", "partial", `${provider}'s call logs give the transcript and every tool call; the model's internal steps aren't exposed.`);
  } else if (code) {
    add("pinning", "supported", "Runs are pinned to the commit they ran.");
  }
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
      family: family ? `${family.family} ${family.versions}` : null,
      familyStatus: family?.status || null,
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

/** A voice world run as text: the agent has no audio path here (code or endpoint, not LiveKit/Pipecat). */
export const isTextRehearsal = (env, agent) => {
  const kind = sourceKindOf(agent);
  return VOICE.includes(env?.surface) && ["repo", "upload", "framework", "endpoint"].includes(kind) && !/livekit|pipecat/i.test(agent?.values?.framework || "");
};
