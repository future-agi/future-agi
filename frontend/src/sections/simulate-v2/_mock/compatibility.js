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
import {
  checksConnection,
  connectionSpecOf,
  startsFromCode,
} from "./connectionCheck";
import { sourceKindOf } from "./agentRegistry";
import { takesSnapshot } from "./candidateSource";
import { proofKindOf } from "./rsiModel";
import { membersOf } from "./improvementPolicy";
import { isConnectOnly } from "./envModel";
import {
  agentToolsFor,
  knownToolsFor,
  requiredToolsOf,
  scenariosCalling,
  toolFit,
} from "./toolFit";
import {
  currentAgentVersion,
  currentEnvVersion,
  isCredentialsVersion,
} from "./versions";
import { hashSeed } from "./runStream";
import { familyOf, transportQualified } from "./testedVersions";
import { checksStateOf } from "./release";

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

/* Read from the environment's build, not from running the agent. */
const BUILD_ROWS = ["toolSchema", "toolsNeeded", "state", "pinning"];

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

const FROM_CHECK = {
  ok: "supported",
  failed: "blocked",
  unavailable: "unavailable",
};
const VOICE = ["voice", "multi"];

/*
  Each fix (TH-8881 §3.3): its type, what would change the status, and what
  checking it again takes — the agent (quick, nothing rebuilt) or the world
  (a rebuild of the environment's bindings, with its cost asked first).
*/
const fixOf = (r, { prod, release, missingNames }) => {
  if (r.status === "supported" || r.blockedBy) return null;
  const id = r.fixId;
  if (id === "start")
    return {
      type: "Startup",
      would: "The start command starts the agent and its health check answers.",
      recheck: "agent",
    };
  if (id === "session")
    return {
      type: "Message mapping",
      would: "Turn 2 keeps the session and the agent remembers turn 1.",
      recheck: "agent",
    };
  if (["reply", "taskId", "status", "artifact"].includes(id))
    return {
      type: "Message mapping",
      would: "A test message comes back read from the field you set.",
      recheck: "agent",
    };
  if (id === "tools")
    return {
      type: "Tool routing",
      would: `${prod[0] || "Each tool"} is pointed at the test world, and a re-check sees its call land there.`,
      recheck: "world",
    };
  if (r.repair === "rebuild")
    return {
      type: "Tool binding",
      would:
        "The environment is rebuilt for the new arguments — a new environment version; earlier runs keep their results.",
      recheck: "world",
    };
  if (r.repair === "agent")
    return {
      type: "Tool routing",
      would: `The agent version has ${missingNames.join(", ")}.`,
      recheck: "agent",
    };
  if (r.repair === "release")
    return {
      type: "Evidence",
      would: `The environment's release checks pass on ${release.version}.`,
      recheck: "world",
    };
  if (r.repair === "testcall")
    return {
      type: "Evidence",
      would: "A test call reaches the agent and answers.",
      recheck: "agent",
    };
  if (r.status === "deprecated")
    return {
      type: "Startup",
      would: "The agent is moved to the provider's current agent model.",
      recheck: "agent",
    };
  return null;
};

/* What the check sent and what came back — the evidence behind a result. */
const checkEvidenceOf = (res, spec, agent, prod) => {
  const msg = spec?.messageField || "message";
  const reply = spec?.replyField || "reply";
  const repo = String(
    agent?.values?.repoUrl || agent?.definition?.repoUrl || "",
  )
    .split("/")
    .pop();
  const host = `api.${repo || "your-service"}.com`;
  switch (res.id) {
    case "starts":
      return [
        { k: "Ran", v: spec?.startCommand || "—" },
        {
          k: "Got",
          v:
            res.state === "ok"
              ? `GET ${spec?.health || "/health"} → 200`
              : res.detail,
        },
      ];
    case "replies":
      return [
        { k: "Sent", v: `{ "${msg}": "Where is my order?" }` },
        { k: "Got", v: `{ "${reply}": "What is your order number?" }` },
      ];
    case "continuity":
      return res.state === "ok"
        ? [
            {
              k: "Turn 2 sent",
              v: `{ "${msg}": "It's ORD-1042", "${spec?.sessionField}": "test-1" }`,
            },
            {
              k: "Got",
              v: `{ "${reply}": "Thanks — ORD-1042 was delivered on 28 Sep." }`,
            },
          ]
        : [
            {
              k: "Turn 1 got",
              v: `{ "${reply}": "What is your order number?", "session_id": "test-1" }`,
            },
            {
              k: "Turn 2 sent",
              v: `{ "${msg}": "It's ORD-1042" } — no session field`,
            },
            { k: "Got", v: `{ "${reply}": "What is your order number?" }` },
          ];
    case "tools":
      return res.state === "ok"
        ? [
            {
              k: "Called",
              v: 'lookup_order(order_id: "ORD-1042") → test world',
            },
          ]
        : [
            {
              k: "Called",
              v: `${prod[0]}(order_id: "ORD-1042", amount: 45.99)`,
            },
            {
              k: "Went to",
              v: `POST https://${host}/v1/${String(prod[0]).replace(/_/g, "-")} — production. The sandbox stopped it; nothing was sent.`,
            },
          ];
    case "cancel":
      return [
        { k: "Sent", v: "cancel mid-turn" },
        { k: "Got", v: res.detail },
      ];
    default:
      return null;
  }
};

/** How long checking the world again takes: a rebuild of its bindings, no run.
    No credit figure — the plan carries no cost estimate for now (CH-9). */
export const recheckCostOf = () => ({ minutes: 2 });

/** The cases a full run of this world takes — or the ones asked for. */
const requestedOf = (envState, scenarioIds) => {
  const live = (envState?.scenarios || []).filter(
    (s) => !s.archived && !s.retired && !s.proposed && !s.canary,
  );
  if (!scenarioIds?.length) return live;
  const want = new Set(scenarioIds);
  return live.filter((s) => want.has(s.id));
};

/**
 * The report for `agent` on `env`: { identity, rows, notNeeded, scope,
 * worst, testedAt, cli }. Each row: { id, label, status, detail, repair?,
 * affected: [scenario ids], blocks: bool, checkedAt?, evidence?, fixId? }
 * — the last three when the connection check decided it: when, what it saw,
 * and which of its repairs fixes it.
 */
export const compatibilityOf = ({
  env,
  envState,
  agent = envState?.agent,
  scenarioIds = null,
  /* The version picked in the run plan; another agent's version is its own
     (ownAgent false), never looked up in this environment's history. */
  agentVersion = null,
  ownAgent = true,
}) => {
  const v = agent?.values || {};
  const kind = sourceKindOf(agent);
  const checked = checksConnection(agent);
  const code = takesSnapshot(agent);
  const test = v.connectionTest || null;
  const results = Object.fromEntries(
    (test?.results || []).map((r) => [r.id, r]),
  );
  /* What the last check found before a fix cleared it: the checks it failed
     read "Fix applied — check to confirm" until the next one runs. */
  const before =
    !test && v.connectionTestBefore
      ? Object.fromEntries(
          (v.connectionTestBefore.results || []).map((r) => [r.id, r]),
        )
      : {};
  /* Checks that failed and later passed — kept so a fix doesn't just vanish. */
  const fixed = Object.fromEntries((v.fixedChecks || []).map((f) => [f.id, f]));
  /* A platform agent's evidence is its test call. */
  const platformTest = v.platformTest || null;
  /* The pair's check for a platform agent, run from Run setup (Karthik's run
     readiness: Re-check). A voice check places no call. */
  const platformCheck = (env?.id && v.platformChecks?.[env.id]) || null;
  /* The environment's release checks prove world reset and hidden checks. */
  const release = checksStateOf(env, envState);
  const releaseAt = release.stale ? null : envState?.releaseChecks?.at || null;
  const surface = env?.surface || "chat";
  const voice = VOICE.includes(surface);
  const connectOnly = isConnectOnly(env);
  const live = proofKindOf(agent).kind === "live";
  const provider = v.provider
    ? v.provider.charAt(0).toUpperCase() + v.provider.slice(1)
    : "the platform";
  const version = agentVersion
    ? { label: agentVersion }
    : currentAgentVersion(envState);
  /* The tested family it belongs to — context, never proof for this connection. */
  const family = familyOf(agent, kind);
  const requested = requestedOf(envState, scenarioIds);
  const all = requested.map((s) => s.id);
  const known = knownToolsFor(env, envState);
  const needs = (tool) =>
    requested
      .filter((s) => requiredToolsOf(s, known).includes(tool))
      .map((s) => s.id);
  const writeCases = requested
    .filter((s) =>
      requiredToolsOf(s, known).some((t) =>
        /refund|charge|pay|cancel|book|transfer|create|update|delete|issue|rebook/i.test(
          t,
        ),
      ),
    )
    .map((s) => s.id);

  /* Stale: evidence that no longer matches — the agent version, the
     environment version or the scenarios moved on after the last check. A
     credentials-only version keeps the check: it behaves as its base. */
  const testedAt = test?.at ? Date.parse(test.at) : null;
  /* Only a version that was actually saved — a stand-in "v1" is dated now. */
  const saved = ownAgent
    ? (envState?.agentVersions || []).find((x) => x.label === version?.label)
    : null;
  const versionAt = Date.parse(saved?.createdAt || saved?.at || "") || null;
  /* What this pairing looked like when it was last checked here: a newer
     environment version or edited scenarios make that check stale too. */
  const checkedHere = env?.id ? v.checkedOn?.[env.id] || null : null;
  const pairNow = checkedHere ? pairingStampOf(env, envState) : null;
  const staleWhy =
    testedAt &&
    versionAt &&
    versionAt > testedAt &&
    !isCredentialsVersion(saved)
      ? `Agent ${version.label} arrived after the last test`
      : checkedHere && pairNow.envVersion !== checkedHere.envVersion
        ? `Environment ${pairNow.envVersion} was saved after the last check (it ran on ${checkedHere.envVersion})`
        : checkedHere && pairNow.suite !== checkedHere.suite
          ? "The scenarios changed after the last check"
          : null;

  const rows = [];
  const add = (id, status, detail, extra = {}) =>
    rows.push({
      id,
      label: CAPABILITIES.find((c) => c.id === id).label,
      status,
      detail,
      affected: [],
      ...extra,
    });
  /*
    A row from the connection check (TH-8874): Supported only on a check that
    passed — never on what kind of agent it is. Untested until a check ran;
    Stale when the check is out of date, saying what it was.
  */
  const fromCheck = (id, checkId, affected, fallback) => {
    if (!checked) {
      /* Built here: it runs here. A platform agent: the pair's check (or a
         test call already made on its page) proves it. */
      if (kind !== "platform") return add(id, "supported", fallback);
      /* A voice check places no call: replies are read from runs, never
         block and never lower the answer (Karthik: message Unavailable for
         voice). */
      if (
        platformCheck &&
        !platformTest &&
        voice &&
        ["message", "continuity"].includes(id)
      )
        return add(
          id,
          "unavailable",
          "A voice check places no call, so replies aren't checked before the run — they're read from the run itself.",
        );
      if (platformTest || platformCheck)
        return add(id, "supported", fallback, {
          checkedAt: (platformTest || platformCheck).at,
          ...(platformTest && {
            evidence: `Test call (${platformTest.mode || "text"}): ${fallback}`,
          }),
        });
      return add(
        id,
        "untested",
        "Not checked for this agent on this environment yet — run the check.",
        { affected, repair: "test" },
      );
    }
    const r = results[checkId];
    if (!r) {
      const was = before[checkId];
      const applied =
        v.fixesApplied === "all" ||
        (v.fixesApplied || []).includes(was?.repair?.id);
      /* A fix makes the last check out of date: Stale, dated by that
         check, until a re-check clears it (TH-8877). */
      const lastAt = v.connectionTestBefore?.at || null;
      if (was?.state === "failed" && applied)
        return add(
          id,
          "stale",
          `Fix applied — re-check to confirm. Before the fix: ${was.detail}`,
          {
            affected,
            repair: "test",
            fixApplied: true,
            fixedCheck: checkId,
            checkedAt: lastAt,
            was: "blocked",
          },
        );
      if (was?.state === "ok")
        return add(
          id,
          "stale",
          "Was Supported. Checked before the fix — re-check to confirm it still holds.",
          { affected, repair: "test", checkedAt: lastAt, was: "supported" },
        );
      /* Not what was fixed: it stands as the last check found it. */
      if (was?.state === "failed")
        return add(id, "blocked", was.detail, {
          affected,
          repair: "connection",
          blocks: true,
          fixId: was.repair?.id || null,
          fromLastCheck: true,
        });
      return add(
        id,
        "untested",
        "No connection check has covered this yet — run it before trusting a run.",
        { affected, repair: "test" },
      );
    }
    if (staleWhy && r.state === "ok")
      return add(
        id,
        "stale",
        `Was Supported. ${staleWhy} — re-check before trusting it.`,
        { affected, repair: "test", checkId, was: "supported" },
      );
    /* A stop that was requested but never seen isn't a pass or a fail. */
    const status =
      r.outcome === "stop-requested"
        ? "untested"
        : FROM_CHECK[r.state] || "untested";
    return add(id, status, r.detail, {
      affected: status === "supported" ? [] : affected,
      ...(status === "blocked" && { repair: "connection", blocks: true }),
      ...(status === "supported" &&
        fixed[checkId] && { fixedAt: fixed[checkId].at, was: "blocked" }),
      checkId,
    });
  };

  /* Start and health — a provider being retired is Deprecated, not broken. */
  if (v.provider === "bland")
    add(
      "start",
      "deprecated",
      "Reachable, but Bland's older pathway agents are being retired on 15 Nov 2026 — move to its new agent model. Results so far stay readable.",
      { repair: "connection" },
    );
  else
    fromCheck(
      "start",
      "starts",
      all,
      kind === "platform"
        ? `Reached through ${provider}'s API.`
        : "Runs here — nothing to start.",
    );
  fromCheck(
    "message",
    "replies",
    all,
    kind === "platform"
      ? `${provider} answers every turn with a mapped reply.`
      : "Replies are mapped.",
  );
  fromCheck(
    "continuity",
    "continuity",
    all,
    "The same session keeps the conversation across turns.",
  );

  const spec = checked ? connectionSpecOf(agent) : null;
  const streamed = spec?.method === "sse" || spec?.method === "ws";
  if (streamed && !transportQualified(family, spec.method))
    add(
      "streaming",
      "unavailable",
      `Unsupported — ${spec.method === "sse" ? "SSE" : "WebSocket"} stays unsupported until an adapter for it passes qualification${family ? `, and ${family.family} hasn't` : ""}. Use HTTP for now.`,
    );
  else if (streamed || voice)
    add(
      "streaming",
      "supported",
      voice
        ? "Audio and partial transcripts stream and end with a completion event."
        : "Partial replies arrive in order and end with a completion event.",
    );
  else
    add(
      "streaming",
      "unavailable",
      `Its connection replies whole over ${spec ? (spec.method || "http").toUpperCase() : "the API"} — no case here needs streaming.`,
    );

  if (spec?.method === "async") fromCheck("files", "artifact", all, "");
  else
    add(
      "files",
      "unavailable",
      "Not exposed by this connection — no case here grades a file.",
    );

  const agentTools = known.length;
  if (kind === "builtin" || kind === "platform" || code)
    add(
      "toolSchema",
      "supported",
      `${agentTools} tools with their arguments and results are known.`,
    );
  else
    add(
      "toolSchema",
      "partial",
      `${agentTools} tools are known from the world; the endpoint doesn't list its own — calls are matched by name and arguments.`,
    );

  /* A tool this agent version calls with arguments the world doesn't answer
     (a new version changed its signature): the cases that call it are
     blocked until the environment is rebuilt for it (connect.md CN-3). */
  const reshaped = toolFit(env, envState, { agent: version?.label }).changed;
  if (reshaped.length) {
    const ids = new Set(requested.map((s) => s.id));
    const hit = scenariosCalling(
      env,
      envState,
      reshaped.map((t) => t.name),
    ).filter((s) => ids.has(s.id));
    const at = rows.findIndex((r) => r.id === "toolSchema");
    if (at >= 0) rows.splice(at, 1);
    add(
      "toolSchema",
      "blocked",
      `${reshaped.map((t) => `${t.name} now takes ${(t.params || []).join(", ")}${t.was?.length ? ` (was ${t.was.join(", ")})` : ""}`).join("; ")} — the world still answers the old arguments. Cases that call ${reshaped.length === 1 ? "it" : "them"} can't run on agent ${version?.label || "this version"} until the environment is rebuilt for ${reshaped.length === 1 ? "it" : "them"}.`,
      {
        affected: hit.map((s) => s.id),
        repair: "rebuild",
        blocks: true,
      },
    );
  }

  /* Tools the cases need that this agent version doesn't have. */
  const agentHas = new Set(
    agentToolsFor(env, envState, version?.label).map((t) => t.name),
  );
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
    add(
      "toolsNeeded",
      "untested",
      "This agent's tools aren't known yet — sync it or run the connection check, then we can say which cases it can't do.",
      { affected: writeCases, repair: "test" },
    );
  else if (missingNames.length)
    add(
      "toolsNeeded",
      "blocked",
      `${agent?.name || "This agent"} ${agentVersionWord(version)}has no ${missingNames.slice(0, 3).join(", ")}${missingNames.length > 3 ? ` or ${missingNames.length - 3} more` : ""} — ${missingNames.length === 1 ? "it's" : "they're"} needed by ${new Set(Object.values(missing).flat()).size} case${new Set(Object.values(missing).flat()).size === 1 ? "" : "s"}, which would fail for a missing tool, not the agent's judgment. Add ${missingNames.length === 1 ? "it" : "them"} to the agent, or run the rest.`,
      {
        affected: [...new Set(Object.values(missing).flat())],
        repair: "agent",
        blocks: true,
      },
    );
  else {
    const used = new Set(requested.flatMap((s) => requiredToolsOf(s, known)))
      .size;
    add(
      "toolsNeeded",
      "supported",
      `The agent has every tool the requested cases call${used ? ` — all ${used}` : ""}.`,
    );
  }

  /* Controlled tool routing — the one that most often blocks cases. */
  /* A production tool the last check found still stands until its own fix. */
  const toolsFixed =
    v.fixesApplied === "all" || (v.fixesApplied || []).includes("tools");
  const prod =
    test?.productionTools ||
    (!test && !toolsFixed ? v.connectionTestBefore?.productionTools : null) ||
    [];
  if (checked && prod.length)
    add(
      "toolRouting",
      "blocked",
      `${prod.join(", ")} still reaches production — its URL is hard-coded. Cases that need it can't run against the test world.`,
      {
        affected: [...new Set(prod.flatMap(needs))],
        repair: "connection",
        blocks: true,
        checkId: "tools",
        fixId: "tools",
      },
    );
  else if (checked) {
    /* The cases the production tool blocked are the ones its fix confirms. */
    const wasProd = (!test && v.connectionTestBefore?.productionTools) || [];
    fromCheck(
      "toolRouting",
      "tools",
      wasProd.length ? [...new Set(wasProd.flatMap(needs))] : writeCases,
      "",
    );
  } else if (live)
    add(
      "toolRouting",
      "partial",
      `Tools run in your ${provider} account. Reads are checked against the world; writes are observed, not controlled.${family?.limits && family.limits !== "—" ? ` ${family.limits}.` : ""}`,
      { affected: writeCases },
    );
  else
    add(
      "toolRouting",
      "supported",
      "Every tool call is answered by the test world.",
    );

  if (connectOnly)
    add(
      "state",
      "unavailable",
      "Your agent calls its own systems, so before-and-after state can't be read here. Graded on the conversation and tool calls.",
      { affected: writeCases },
    );
  else if (live)
    add(
      "state",
      "partial",
      "The world's records are checked; anything written in your account isn't visible.",
      { affected: writeCases },
    );
  else
    add(
      "state",
      "supported",
      "Before and after state of the seeded world is captured each trial.",
    );

  const notNeeded = [];
  if (voice) {
    if (kind === "platform") {
      const a = (platformTest?.results || []).find((r) => r.id === "audio");
      if (a)
        add(
          "audio",
          a.status === "supported" ? "supported" : a.status,
          a.line,
          {
            checkedAt: platformTest.at,
            ...(a.status !== "supported" && { affected: all }),
          },
        );
      else
        add(
          "audio",
          "unavailable",
          "Latency and barge-in are measured from this agent's calls in a run — none here yet, so they read unavailable, not zero.",
        );
    } else if (/livekit|pipecat/i.test(v.framework || ""))
      add(
        "audio",
        "supported",
        "Real audio with timing — latency and barge-in are measured.",
      );
    else
      add(
        "audio",
        "partial",
        "Text rehearsal only — dialogue and tools are checked; acoustic latency and barge-in stay unavailable, not zero.",
        { affected: all },
      );
  } else notNeeded.push("Audio and timing");
  if (surface === "browser")
    add(
      "browser",
      "untested",
      "Browser execution isn't certified yet — results here are untested, not supported.",
      { affected: all },
    );
  else notNeeded.push("Browser interaction");
  const members = membersOf(agent);
  if (members.length > 1)
    add(
      "children",
      "partial",
      `The coordinator and ${members.length - 1} workers report delegation and return. Skill activation is unknown without runtime evidence.`,
    );
  else notNeeded.push("Child agents and skills");

  /* Platform agents: what the provider lets us pin, transfer and see. */
  if (kind === "platform") {
    if (v.providerPinned)
      add(
        "pinning",
        "supported",
        `Runs are pinned to ${provider} ${v.providerVersion}.`,
      );
    else if (v.providerVersion)
      add(
        "pinning",
        "partial",
        `${provider} ${v.providerVersion} is a draft — it can change between runs, so results are marked unpinned.`,
      );
    else
      add(
        "pinning",
        "unavailable",
        `${provider} doesn't version this agent. ${version?.label || "v1"} is our snapshot of its config — if it changes on ${provider}, runs are marked unpinned until you sync.`,
      );
    if (voice) {
      const t = v.platformTest;
      if (t?.transfer === "tested")
        add(
          "transfer",
          "supported",
          `A phone test reached the transfer — it rang the test destination ${t.destination || ""} and connected.`.replace(
            / +/g,
            " ",
          ),
        );
      else
        add(
          "transfer",
          "unavailable",
          "A transfer is seen when a run's call reaches one — none here yet. It never holds a case back.",
        );
    }
    add(
      "trace",
      "partial",
      `${provider}'s call logs give the transcript and every tool call; the model's internal steps aren't exposed.`,
    );
  } else if (code) {
    add("pinning", "supported", "Runs are pinned to the commit they ran.");
  }
  /*
    Stop and reset by what the connection can really do (TH-8890): Supported
    only where we run the agent and confirm it; an agent hosted elsewhere is
    Partial, saying what that means for spend and trial independence.
  */
  const hostedElsewhere =
    kind === "platform" || (checked && !startsFromCode(agent));
  if (checked && startsFromCode(agent)) fromCheck("cancel", "cancel", [], "");
  else if (kind === "platform")
    add(
      "cancel",
      "partial",
      `Stopping ends our side; nothing ends the ${provider} call yet, so it runs until it ends on its own. A stopped run reads Cancel requested until ${provider} confirms the call ended.`,
    );
  else if (hostedElsewhere)
    add(
      "cancel",
      "partial",
      "We stop sending and stop watching; your service may still finish the request it already has. A stopped run reads Cancel requested, not Cancelled.",
    );
  else
    add(
      "cancel",
      "supported",
      "It runs here, so a stop is confirmed when it lands.",
    );
  if (connectOnly)
    add(
      "reset",
      "unavailable",
      "A live system isn't assumed resettable — every attempt starts from whatever state it's in.",
      { affected: all },
    );
  else if (hostedElsewhere)
    add(
      "reset",
      "partial",
      "The world resets between trials, but your agent may keep its own memory from one trial into the next — they may not be independent.",
    );
  else if (release.results.resets === "passed")
    add(
      "reset",
      "supported",
      `The agent and its tools start every trial from the same seed — the Resets release check passed on ${release.version}.`,
      { checkedAt: releaseAt },
    );
  else if (release.results.resets === "failed")
    add(
      "reset",
      "blocked",
      `Two sessions in a row didn't start from the same state on ${release.version} — trials aren't independent.`,
      { affected: all, repair: "release", blocks: true, checkedAt: releaseAt },
    );
  else
    add(
      "reset",
      "untested",
      `The Resets release check hasn't run on ${release.version} — run the release checks to prove every trial starts from the same state.`,
      { affected: all, repair: "release" },
    );
  add(
    "resume",
    "partial",
    "Unfinished trials restart from their seed — the agent's memory isn't restored.",
  );
  if (code || kind === "endpoint")
    add(
      "cost",
      "partial",
      "Our credits are measured; your model's spend on your own key is unknown — shown as unknown, not zero.",
    );
  else if (kind === "platform")
    add(
      "cost",
      "supported",
      `${provider}'s per-minute cost is read from its API.`,
    );
  else add("cost", "supported", "Every call is metered here.");

  /* A row the connection check decided says when, what it saw, and the fix
     it offered — the Fix on the report opens that same repair. */
  rows.forEach((r) => {
    const res = r.checkId && results[r.checkId];
    if (!res) return;
    r.checkedAt = test?.at || null;
    r.evidence = `${res.label}: ${res.detail}`;
    r.evidenceSteps = checkEvidenceOf(res, spec, agent, prod);
    r.fixId = res.repair?.id || r.fixId || null;
    r.fixLabel = res.repair?.label || null;
  });
  /* A fix applied since: which repair it was, so its re-check scope is known. */
  rows.forEach((r) => {
    if (r.fixedCheck) r.fixId = before[r.fixedCheck]?.repair?.id || null;
  });
  /* What the environment's build established is dated by that build —
     never a Supported with no date behind it. */
  const builtAt = currentEnvVersion(env, envState)?.createdAt || null;
  rows.forEach((r) => {
    if (
      BUILD_ROWS.includes(r.id) &&
      !r.checkedAt &&
      builtAt &&
      (r.status === "supported" || r.status === "partial")
    ) {
      r.checkedAt = builtAt;
      r.fromBuild = true;
    }
  });

  /*
    Gates (TH-8874 §3.3): when a capability others depend on isn't Supported
    or Partial, what depends on it reads Untested naming it — one cause, not
    fifteen. Only what needs the agent running is gated; what's read from its
    definition (its tools, its pinning) or proved by the world stays.
  */
  const RUNTIME = [
    "message",
    "continuity",
    "streaming",
    "files",
    "toolRouting",
    "state",
    "audio",
    "browser",
    "children",
    "transfer",
    "trace",
    "cancel",
    "resume",
  ];
  const GATES = [
    ["start", (r) => RUNTIME.includes(r.id)],
    ["message", (r) => ["continuity", "streaming"].includes(r.id)],
    ["toolSchema", (r) => r.id === "toolRouting"],
  ];
  /* Waiting on a re-check after a fix: the last check already says what
     each row was, so nothing cascades. */
  const pendingRecheck = !test && !!v.connectionTestBefore;
  GATES.forEach(([gid, gated]) => {
    if (pendingRecheck) return;
    const g = rows.find((r) => r.id === gid);
    if (!g || ["supported", "partial"].includes(g.status)) return;
    rows.forEach((r) => {
      /* A known limit (Partial, Unavailable) holds whatever the check says. */
      if (
        r === g ||
        !gated(r) ||
        r.status === "unavailable" ||
        r.status === "partial" ||
        r.fixApplied ||
        r.blockedBy
      )
        return;
      Object.assign(r, {
        status: "untested",
        blockedBy: g.label,
        detail: `Waits on ${g.label.toLowerCase()} — ${g.status === "blocked" ? "fix that first" : "check that first"}.`,
        blocks: false,
        repair: null,
        fixId: null,
        fixApplied: false,
        evidence: null,
        evidenceSteps: null,
        checkedAt: null,
      });
    });
  });

  /* A fix named as the thing to do. */
  rows.forEach((r) => {
    if (r.fixId === "tools" && prod[0])
      r.fixLabel = `Point ${prod[0]} at the test world`;
    else if (!r.fixLabel && r.fromLastCheck)
      r.fixLabel = before[r.checkId]?.repair?.label || null;
    /* A change made elsewhere is named for what it changes. */
    if (!r.fixLabel && r.repair === "agent" && missingNames.length)
      r.fixLabel = `Add ${missingNames.slice(0, 2).join(", ")}${missingNames.length > 2 ? ` and ${missingNames.length - 2} more` : ""} to the agent`;
    if (!r.fixLabel && r.status === "deprecated")
      r.fixLabel = "Move the agent to the current model";
  });
  /* Each row that isn't Supported carries its fix (TH-8881 §3.3). */
  rows.forEach((r) => {
    const f = fixOf(r, { prod, release, missingNames });
    if (f) r.fix = f;
    /* From the live reply: what the session field should be. */
    if (r.fixId === "session" && r.status === "blocked")
      r.suggestion = {
        label: "Use session_id",
        why: "Turn 1's reply carried session_id = test-1 — that's the field to send back.",
        connection: { sessionField: "session_id" },
      };
  });

  /* Rows in the order the roadmap lists them. */
  rows.sort(
    (a, b) =>
      CAPABILITIES.findIndex((c) => c.id === a.id) -
      CAPABILITIES.findIndex((c) => c.id === b.id),
  );

  const blockedIds = new Set(
    rows.filter((r) => r.blocks).flatMap((r) => r.affected),
  );
  const blocked = requested
    .filter((s) => blockedIds.has(s.id))
    .map((s) => ({
      id: s.id,
      title: s.title || s.name || s.id,
      by: rows
        .filter((r) => r.blocks && r.affected.includes(s.id))
        .map((r) => r.label),
    }));
  /* Untested: needs a capability no check has proved, and nothing it needs
     is blocked (TH-8881 §3.2). requested = eligible + blocked + untested. */
  const untestedIds = new Set(
    rows
      .filter((r) => r.status === "untested" || r.status === "stale")
      .flatMap((r) => r.affected)
      .filter((id) => !blockedIds.has(id)),
  );
  const untested = requested
    .filter((s) => untestedIds.has(s.id))
    .map((s) => ({ id: s.id, title: s.title || s.name || s.id }));
  const worst = rows.reduce(
    (w, r) =>
      COMPAT_STATUS[r.status].rank > COMPAT_STATUS[w].rank ? r.status : w,
    "supported",
  );
  const connectionRev = checked
    ? `conn-${(hashSeed(JSON.stringify(spec)) >>> 0).toString(16).slice(0, 6)}`
    : null;

  return {
    identity: {
      agent: agent?.name || "Agent",
      agentVersion: version?.label || "v1",
      connection: connectionRev,
      world: env?.name,
      worldVersion: currentEnvVersion(env, envState)?.label || "v1",
      release: env?.release || null,
      mode: ["Hosted", live ? "Live" : "Simulated"].join(" · "),
      family: family ? `${family.family} ${family.versions}` : null,
      familyStatus: family?.status || null,
    },
    rows,
    notNeeded,
    scope: {
      requested: requested.length,
      eligible: requested.length - blocked.length - untested.length,
      blocked,
      untested,
      /* What a reviewed run can take: everything not blocked. */
      runnable: requested.length - blocked.length,
      eligibleIds: requested
        .filter((s) => !blockedIds.has(s.id))
        .map((s) => s.id),
    },
    counts: Object.fromEntries(
      Object.keys(COMPAT_STATUS).map((k) => [
        k,
        rows.filter((r) => r.status === k).length,
      ]),
    ),
    worst,
    testedAt: test?.at || null,
    /* The last check before a fix cleared it — never "never checked". */
    checkedBeforeAt: !test ? v.connectionTestBefore?.at || null : null,
    staleWhy,
    validation: validationOf(v, test, testedAt, rows, staleWhy),
    fromCode: startsFromCode(agent),
  };
};

/** The scenarios a full run takes, as one short fingerprint. */
const suiteOf = (envState) =>
  (
    hashSeed(
      JSON.stringify(
        requestedOf(envState).map((s) => [
          s.id,
          s.title || s.name || "",
          s.goal || s.description || s.prompt || "",
          (s.subgoals || s.subGoals || []).length,
        ]),
      ),
    ) >>> 0
  ).toString(16);

/** Saved with a check run from an environment: the pairing it covered. */
export const pairingStampOf = (env, envState) => ({
  envVersion: currentEnvVersion(env, envState)?.label || "v1",
  suite: suiteOf(envState),
});

/* How long a pairing check may take before it gives up (minutes). */
export const PAIR_CHECK_LIMIT_MIN = 45;

/*
  The pairing's last check, as a run reads it: ready, stale, failed (runs
  of the pairing are refused until a check passes), timed out (it didn't
  finish within PAIR_CHECK_LIMIT_MIN, which fails the pairing the same way),
  or unavailable — the check failed on our side (the test world couldn't be
  prepared), so it counts against nothing and holds no run. "validating" is
  the live state, passed in by the caller.
*/
const validationOf = (v, test, testedAt, rows, staleWhy) => {
  const off = v.checkUnavailable;
  if (off && (!testedAt || Date.parse(off.at) > testedAt))
    return {
      state: off.timedOut ? "timed_out" : "unavailable",
      reason: off.reason,
      at: off.at,
    };
  if (!test) return null;
  /* The agent's check fails only when it couldn't start or reply; any other
     blocked row (a tool still reaching production, say) holds back just the
     cases that need it. */
  const failed = rows.filter(
    (r) => r.status === "blocked" && (r.id === "start" || r.id === "message"),
  );
  if (failed.length)
    return {
      state: "failed",
      reason: failed.map((r) => r.label.toLowerCase()).join(", "),
      at: test.at,
    };
  return { state: staleWhy ? "stale" : "ready", at: test.at };
};

/**
 * The report sorted by what it asks of you (TH-8877 feedback: an action page,
 * not a report): needs — something to fix; waiting — no check has proved it
 * yet (or a fix waits to be confirmed); limits — what this kind of connection
 * can't do, nothing to fix; works — Supported.
 */
export const capabilityGroupsOf = (report) => {
  const rows = report?.rows || [];
  return {
    needs: rows.filter(
      (r) =>
        r.status === "blocked" ||
        r.status === "deprecated" ||
        (r.repair === "agent" && r.status !== "supported"),
    ),
    waiting: rows.filter(
      (r) => r.status === "untested" || r.status === "stale",
    ),
    limits: rows.filter(
      (r) => r.status === "partial" || r.status === "unavailable",
    ),
    works: rows.filter((r) => r.status === "supported"),
  };
};

/**
 * The one answer at the top — can this agent be tested in this world — and
 * the one thing to do next. kind: checking | unchecked | blocked | stale |
 * ready; action: { kind: fix | check | testcall | release | run, row? }.
 */
export const capabilityAnswerOf = (report, { checkable, testing } = {}) => {
  const { needs, waiting } = capabilityGroupsOf(report);
  const { requested, blocked, untested } = report.scope;
  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const fixable = needs.find(
    (r) =>
      r.repair === "connection" ||
      r.repair === "agent" ||
      r.repair === "release",
  );
  const applied = waiting.filter((r) => r.fixApplied);
  const onCheck = waiting.filter((r) => r.repair === "test");
  const onRelease = waiting.filter((r) => r.repair === "release");
  const onCall = waiting.filter((r) => r.repair === "testcall");
  if (testing)
    return {
      kind: "checking",
      title: "Checking the connection…",
      body: "Starting the agent, sending two turns and calling its tools against the test world.",
    };
  if (blocked.length)
    return {
      kind: "blocked",
      title: `${blocked.length} of ${plural(requested, "case")} can't run — ${plural(needs.filter((r) => r.blocks).length || needs.length, "problem")} to fix`,
      body:
        requested - blocked.length
          ? `The other ${requested - blocked.length} don't depend on ${needs.length === 1 ? "it" : "them"}. Fix it, then re-check — or run just those from the run plan.`
          : `None can run until ${needs.filter((r) => r.blocks).length > 1 ? "they're" : "it's"} fixed.`,
      action: fixable ? { kind: "fix", row: fixable } : null,
    };
  if (applied.length)
    return {
      kind: "stale",
      title: "Fix applied — re-check to confirm",
      body: `Checking again confirms ${applied.map((r) => r.label.toLowerCase()).join(", ")} and everything the check covers.`,
      action: { kind: "check", again: true },
    };
  if (checkable && !report.testedAt)
    return {
      kind: "unchecked",
      title: "Not checked yet",
      body: `Run the connection check before trusting a run. Until then ${plural(untested.length, "case")} ${untested.length === 1 ? "is" : "are"} untested — they'd run, but nothing proves they can be graded.`,
      action: { kind: "check" },
    };
  if (onCall.length)
    return {
      kind: "unchecked",
      title: "No test call yet",
      body: "Only a test call proves this agent answers and what it lets us measure. Everything that needs it running waits on one.",
      action: { kind: "testcall", row: onCall[0] },
    };
  if (onCheck.length)
    return {
      kind: "stale",
      title: report.staleWhy
        ? "Checked before something changed"
        : `${plural(onCheck.length, "capability", "capabilities")} not checked yet`,
      body: report.staleWhy
        ? `${report.staleWhy} — re-check before trusting a run.`
        : "Re-check so every capability the cases need is covered.",
      action: { kind: "check" },
    };
  if (onRelease.length)
    return {
      kind: "stale",
      title: "The environment's release checks haven't run",
      body: `${onRelease.map((r) => r.label).join(" and ")} ${onRelease.length === 1 ? "is" : "are"} proved by the environment, not the agent. Until its release checks run, ${plural(untested.length, "case")} stay untested.`,
      action: { kind: "release", row: onRelease[0] },
    };
  if (needs.length)
    return {
      kind: "stale",
      title: `${plural(needs.length, "thing")} to look at`,
      body: "Every case can run, but this connection has something to sort out first.",
      action: fixable ? { kind: "fix", row: fixable } : null,
    };
  return {
    kind: "ready",
    title: `Ready — all ${plural(requested, "case")} can run`,
    body: "Every capability the cases need is proved here. The limits below are what this connection can't grade.",
    action: { kind: "run" },
  };
};

/** "4 of 10 cases" — the requested count stays the denominator. */
/** What to call the cases waiting on a check: "stale" when everything they
    wait on was checked before something changed, else "untested". */
export const waitingWordOf = (report) => {
  const waiting = (report?.rows || []).filter(
    (r) =>
      (r.status === "untested" || r.status === "stale") && r.affected.length,
  );
  return waiting.length && waiting.every((r) => r.status === "stale")
    ? "stale"
    : "untested";
};

export const affectsLabel = (n, of) =>
  `${n} of ${of} case${of === 1 ? "" : "s"}`;

/** The report for an agent record on one of its environments: the agent as
    connected now (its latest check) on that world. */
export const agentReportFor = (agent, env, envState) =>
  env && envState
    ? compatibilityOf({
        env,
        envState,
        agent: {
          ...(envState.agent || {}),
          ...agent,
          values: {
            ...(envState.agent?.values || {}),
            ...(agent?.values || {}),
          },
        },
      })
    : null;

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
  testedAt: report.testedAt,
  validation: report.validation,
  staleWhy: report.staleWhy,
  rows: report.rows.map((r) => ({
    id: r.id,
    label: r.label,
    status: r.status,
    detail: r.detail,
    affected: r.affected.length,
  })),
});

export const doctorCli = (agent, env) =>
  `fi doctor --agent "${agent?.name || "agent"}" --world ${env?.id}`;

/** A voice world run as text: the agent has no audio path here (code or endpoint, not LiveKit/Pipecat). */
export const isTextRehearsal = (env, agent) => {
  const kind = sourceKindOf(agent);
  return (
    VOICE.includes(env?.surface) &&
    ["repo", "upload", "framework", "endpoint"].includes(kind) &&
    !/livekit|pipecat/i.test(agent?.values?.framework || "")
  );
};
