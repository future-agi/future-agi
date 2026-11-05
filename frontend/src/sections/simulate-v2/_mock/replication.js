/*
  How the platform makes its copy of a connected agent (PRD v6, Story 1 —
  "replication is the platform's job, not the user's").

  The user points at the agent as it runs in production; we make the copy a
  simulation drives, and say how we made it and what that copy can prove:

    sandbox_build    — code at a pinned commit, built fresh from source and
                       run in our sandbox, its tools answered by the seeded
                       world (sealed)
    adapter_call     — a framework agent served by our adapter at a URL we
                       don't control, with no repository linked: we call it
                       where serve() runs (Deployed by you)
    provider_clone   — Vapi: a test copy of the assistant in the customer's
                       account, called with its real tools (Phase 1, live)
    platform_draft   — Retell: a draft version called with that version
                       pinned, once the live numbers are pinned to a released
                       version; until then settings overridden for one call
    platform_branch  — ElevenLabs and Bland (Phase 2): a branch of the agent
    imported_copy    — Vapi or Retell with the code behind its tools uploaded:
                       the assistant's config imported into our sandbox, its
                       tool code run against the seeded world (sealed).
                       Built-in platform actions (transfers) still run for real
    project_call     — LiveKit or Pipecat connected by project: we call the
                       running agent; a fix goes through its code
    local_sdk        — local code through our SDK: it runs on the customer's
                       machine and checks in with its commit and a fingerprint
                       of uncommitted changes (Run on your machine)
    exported_mocked  — kept for saved records: a key that can't create a copy,
                       so each test call overrides the live assistant's
                       settings for that one call
    run_here         — built in Future AGI, or a pasted prompt: it already
                       runs here
    wrapped_endpoint — an opaque URL: it can't be copied, so simulator traffic
                       drives the real agent (and its real tools) — only after
                       an explicit acknowledgment
    phone_call       — a phone number: we dial it; test only

  Run fidelity follows from the mode and is never mixed in one aggregate:
    real-state · live · mocked · observed-only · observed+webhook
  and every copy carries its proof kind (Sealed · Live · Prompt only) from
  rsiModel's proofKindOf.

  Stored on the agent's values (all optional — defaults derive from source):
    values.replicationMode  — an override, e.g. exported_mocked after a key
                              without create scope
    values.runLocation      — "hosted" (code we build) or "sdk" (the
                              customer's own machine, through our SDK). "sdk"
                              counts only with values.localSdk — the agent
                              checked in from that machine; an older stored
                              "sdk" without it reads as hosted.
    values.localSdk         — set when the SDK checked in (framework agents)
    values.endpointConsent  — { acknowledgedAt, webhookAt|null } (endpoints)
*/
import { definitionOf, sourceKindOf } from "./agentRegistry";
import { PLATFORM_TESTING, isLocalSdkAgent, proofKindOf, retellPinStateOf } from "./rsiModel";

/* Vapi makes a test copy, which needs a key with create scope. */
const CLONE_PROVIDERS = new Set(["vapi"]);
const BRANCH_PROVIDERS = new Set(["elevenlabs", "bland"]);
/* Platforms that hold unpublished draft versions of an agent. */
const VERSION_PROVIDERS = new Set(["telnyx", "google_cx", "voiceflow", "agentforce", "copilot_studio"]);
/* Code frameworks connected by their project — called, not copied. */
const PROJECT_PROVIDERS = new Set(["livekit", "pipecat"]);

/** A phone number we can only dial — however it was connected. */
export const isPhoneAgent = (agent) => !!(agent?.values?.phoneNumber || agent?.values?.contact?.mode === "phone-only" || agent?.sourceKind === "phone");

export const REPLICATION_MODES = {
  sandbox_build: {
    label: "Sandbox build",
    how: "Built fresh from a read-only snapshot of your code at the pinned commit, and run in our sandbox. Its tools answer from the environment's seeded world, so side effects stay inside.",
    untouched: "Nothing — we only read the code.",
    fidelity: "real-state",
  },
  adapter_call: {
    label: "Called where it runs",
    how: "We call the agent where serve() runs, so its real tools run. We can't build it ourselves — link its repository to get it built fresh in our sandbox.",
    untouched: "Your agent's code and deployment — we only call it.",
    fidelity: "live",
  },
  provider_clone: {
    label: "Test copy",
    how: "A test copy of the assistant in your account, called with its real tools. Where a copy can't be made, we override its settings for one call instead.",
    untouched: "Your live assistant — its settings are checked unchanged before and after every run.",
    fidelity: "live",
  },
  platform_draft: {
    label: "Pinned draft version",
    how: "A draft version in your Retell account, called with that version pinned and its real tools.",
    howUnpinned: "Your live numbers still follow the newest version, so a new draft would take real calls. Until they're pinned to a released version, each test call runs the agent with its settings overridden for that one call.",
    untouched: "Your live numbers — they stay on the released version they're pinned to.",
    fidelity: "live",
  },
  platform_version: {
    label: "Draft version",
    how: "An unpublished draft version of the agent in your account, called with its real tools. Nothing is published until you publish it.",
    untouched: "Your live agent — the draft isn't published.",
    fidelity: "live",
  },
  platform_branch: {
    label: "Branch",
    how: "A branch of the agent in your account, called with its real tools. It isn't merged into the live agent until you merge it.",
    untouched: "Your live agent — nothing is merged into it.",
    fidelity: "live",
    phase: 2,
  },
  imported_copy: {
    label: "Imported copy",
    how: "The assistant's config imported into our sandbox, with the tool code you uploaded answering against the seeded world. Built-in platform actions — transfers, end call, voicemail — still run for real.",
    untouched: "Your live assistant — the copy runs in our sandbox, not in your account.",
    fidelity: "real-state",
  },
  project_call: {
    label: "Called in your project",
    how: "Connected by its project, so we call the running agent and its real tools run. A fix goes through its code — connect the repository to get proven changes.",
    untouched: "Your agent's code and deployment — we only place calls.",
    fidelity: "live",
  },
  local_sdk: {
    label: "Run on your machine",
    how: "Our SDK runs your local code on your machine against the environment's scenarios, and reports the commit it's on plus a fingerprint of uncommitted changes. We score every conversation.",
    untouched: "Your code — your own coding agent makes the changes; we send scenarios and score.",
    fidelity: "local",
  },
  exported_mocked: {
    label: "Settings overridden for one call",
    how: "This key can't create a test copy, so each test call runs your live assistant with its settings overridden for that one call. Its real tools run.",
    untouched: "Your live assistant's saved settings — the override lasts one call.",
    fidelity: "live",
  },
  run_here: {
    label: "Runs in Future AGI",
    how: "Defined here, so it runs here — there is nothing external to copy.",
    untouched: "Nothing external exists.",
    fidelity: "real-state",
  },
  wrapped_endpoint: {
    label: "Wrapped endpoint",
    how: "We only see a URL, so it can't be copied. Simulator traffic drives your real agent, and its real tools execute.",
    untouched: "Nothing is copied — your real agent answers every call.",
    fidelity: "observed-only",
  },
  phone_call: {
    label: "Called on its number",
    how: "We dial the number with the instructions you pasted as the reference. It can't be copied, so simulations call your live agent and its real tools run. Test only — an improvement ends with a suggested fix.",
    untouched: "Nothing is copied — your agent answers every call.",
    fidelity: "observed-only",
  },
};

export const RUN_FIDELITY = {
  "real-state": { label: "Real state", note: "Tools read and write the environment's world." },
  live: { label: "Live", note: "Runs in your account and its tools reach your real systems. We see the conversation and the tool calls the platform reports." },
  local: { label: "On your machine", note: "Runs where your code runs, with whatever its tools reach there. We see the conversation and the tool calls our SDK reports." },
  mocked: { label: "Mocked", note: "Tools answer from mocks — results can't show what a real backend would have done." },
  "observed-only": { label: "Observed only", note: "We see the conversation, not the tool calls behind it." },
  "observed+webhook": { label: "Observed + webhook", note: "Call and tool events are mirrored to us by the webhook you added." },
};

/* Where a code agent runs: built by us in the hosted sandbox, or on the
   customer's own machine through our SDK (connected from the framework flow). */
export const RUN_LOCATIONS = {
  hosted: { label: "Hosted sandbox", short: "Hosted", note: "We build the agent fresh from its code for every run. Its tools answer from the environment's seeded world, so nothing reaches your real systems. Metered in sandbox minutes." },
  sdk: { label: "Run on your machine", short: "Your machine", note: "Our SDK runs your local code on your machine and reports the commit plus a fingerprint of uncommitted changes. Your own coding agent makes changes; we prove them with our scenarios and scoring." },
};

/* A framework agent with a linked repository can be built from it; one only
   served by the adapter is called where it runs. */
const builtFromCode = (agent) => {
  const kind = sourceKindOf(agent);
  return kind === "repo" || kind === "upload" || (kind === "framework" && !!definitionOf(agent).repoUrl);
};

/** Code agents say where they run; everything else runs where it is copied. */
export const choosesRunLocation = (agent) => builtFromCode(agent) || isLocalSdkAgent(agent);

/** Your own machine once the SDK checked in from it; otherwise the hosted sandbox. */
export const recommendedRunLocation = (agent) => (isLocalSdkAgent(agent) ? "sdk" : "hosted");

/* How each hosted platform tests a candidate (PRD Phase 1 and 2). */
const platformMode = (provider) => (provider === "retell" ? "platform_draft"
  : BRANCH_PROVIDERS.has(provider) ? "platform_branch"
  : VERSION_PROVIDERS.has(provider) ? "platform_version"
    : PROJECT_PROVIDERS.has(provider) ? "project_call"
      : "provider_clone");

const defaultMode = (agent) => {
  const kind = sourceKindOf(agent);
  const v = agent?.values || {};
  if (isPhoneAgent(agent)) return "phone_call";
  /* A live assistant on a provider is tested there, even when what we read
     back from it is just its prompt. */
  if (v.provider && v.provider !== "other" && v.agentId) {
    /* Imported with its tool code: a sealed copy in our sandbox. */
    if (v.importedCopy && v.toolCodeUploaded) return "imported_copy";
    return platformMode(v.provider);
  }
  /* A pasted prompt runs here too — whatever provider it was pasted under. */
  if (kind === "builtin" || kind === "prompt" || v.provider === "other") return "run_here";
  if (kind === "platform") return platformMode(v.provider);
  if (kind === "endpoint") return "wrapped_endpoint";
  if (isLocalSdkAgent(agent)) return "local_sdk";
  if (kind === "framework" && !builtFromCode(agent)) return "adapter_call";
  return "sandbox_build";
};

/**
 * How this agent is copied, what that copy can prove, and where it runs.
 * { mode, label, how, untouched, fidelity, fidelityLabel, fidelityNote,
 *   runLocation, consent, needsConsent }
 */
export const replicationOf = (agent, env) => {
  const v = agent?.values || {};
  const mode = v.replicationMode || defaultMode(agent);
  const meta = REPLICATION_MODES[mode] || REPLICATION_MODES.sandbox_build;
  const consent = v.endpointConsent || null;
  const fidelity = mode === "wrapped_endpoint" && consent?.webhookAt ? "observed+webhook" : meta.fidelity;
  /* A stored "sdk" without the SDK's check-in reads as the hosted sandbox —
     we build it from the code ourselves. */
  const runLocation = choosesRunLocation(agent) ? recommendedRunLocation(agent, env) : null;
  const proof = proofKindOf(agent);
  /* Retell: a draft only once the live numbers are pinned. */
  const pins = mode === "platform_draft" ? retellPinStateOf(agent) : null;
  const platform = PLATFORM_TESTING[v.provider] || null;
  const how = mode === "platform_draft" && pins && !pins.pinned ? meta.howUnpinned
      : mode === "platform_branch" && platform ? `${meta.how} ${platform.tests}.${platform.needsVersioning ? " Needs versioning switched on for the agent." : ""}`
        : meta.how;
  return {
    mode,
    ...meta,
    how,
    /* Sealed · Live · Prompt only, and who ran it — said next to the copy. */
    proofKind: proof.kind,
    proofLabel: proof.label,
    proofNote: proof.note,
    tierLabel: proof.tierLabel,
    phase: meta.phase || platform?.phase || 1,
    ...(pins && { pins }),
    fidelity,
    fidelityLabel: RUN_FIDELITY[fidelity].label,
    fidelityNote: RUN_FIDELITY[fidelity].note,
    runLocation,
    consent,
    /* An opaque endpoint can't run until someone says the real agent may be driven. */
    needsConsent: mode === "wrapped_endpoint" && !consent?.acknowledgedAt,
  };
};

/* ── connect-flow and build helpers ───────────────────────────────────────── */

/* Icons for the copy and what it can prove — neutral; the label carries it. */
export const MODE_ICONS = {
  sandbox_build: "solar:server-square-linear",
  adapter_call: "solar:plug-circle-linear",
  provider_clone: "solar:copy-linear",
  platform_draft: "solar:pin-linear",
  platform_branch: "solar:branching-paths-up-linear",
  platform_version: "solar:document-add-linear",
  imported_copy: "solar:box-linear",
  project_call: "solar:phone-calling-rounded-linear",
  local_sdk: "solar:laptop-2-linear",
  exported_mocked: "solar:tuning-2-linear",
  phone_call: "solar:phone-linear",
  run_here: "solar:widget-add-linear",
  wrapped_endpoint: "solar:global-linear",
};
export const FIDELITY_ICONS = {
  "real-state": "solar:database-linear",
  live: "solar:pulse-2-linear",
  local: "solar:laptop-2-linear",
  mocked: "solar:test-tube-linear",
  "observed-only": "solar:eye-linear",
  "observed+webhook": "solar:eye-linear",
};

/** Providers we test on a copy we create — the key needs create scope. */
export const canCloneProvider = (provider) => CLONE_PROVIDERS.has(provider);

/**
 * Prototype stand-in for "the key can read assistants but not create them":
 * a key containing "readonly" (or "read-only", "ro_") behaves that way, so the
 * demo can show the branch.
 */
export const keyLacksCreateScope = (key) => /read[-_]?only|(^|[^a-z])ro_/i.test(String(key || ""));

/** Why a code agent runs where it does. */
export const runLocationReason = (location = "hosted") => (location === "sdk"
  ? "Your agent checks in from your machine with the commit it's on plus a fingerprint of uncommitted changes — we score what it runs"
  : "We build it fresh from the code for every run. Running locally is coming soon with ALK, our open-source toolkit");

const slug = (s) => String(s || "env").toLowerCase().replace(/\s+environment$/, "").replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "env";
const shortHash = (s) => {
  let h = 0;
  const str = String(s || "");
  for (let i = 0; i < str.length; i += 1) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  return Math.abs(h).toString(36).slice(0, 6);
};

/** The labelled clone a build makes in the provider: fagi-sim-<env-slug>-<build>. */
export const cloneNameFor = (env, build = 1) => `fagi-sim-${slug(env?.name || env?.id)}-${build}`;

/**
 * Where an opaque endpoint's production agent mirrors its events — one URL
 * per project, so a webhook added once covers every environment testing it.
 */
export const webhookUrlFor = (agent) => {
  const v = agent?.values || {};
  const project = v.observability?.project || slug(agent?.baseName || agent?.name || v.endpoint || agent?.key);
  return `https://hooks.futureagi.com/v1/sim/${project}/${shortHash(agent?.key || v.endpoint || project)}`;
};

/* Where to add a secondary webhook, per stack — a line each, not a guide. */
export const WEBHOOK_PROVIDERS = [
  { id: "vapi", label: "Vapi", steps: "Assistant → Advanced → Server URL. Add this as a second server URL; keep your own as the first." },
  { id: "retell", label: "Retell", steps: "Agent → Webhook settings → Add webhook. Subscribe to call_started, call_ended and call_analyzed." },
  { id: "bland", label: "Bland", steps: "Pathway or phone number → Webhooks → Add URL. Leave your existing webhook in place." },
  { id: "elevenlabs", label: "ElevenLabs", steps: "Agent → Advanced → Post-call webhook. Add it alongside any existing one." },
  { id: "server", label: "Your server", steps: "POST each turn's tool calls and results here as JSON, after your own handlers run. Fire and forget — we never respond with data." },
];

/**
 * What a build records about the copy it ran against, so a result can say
 * what kind of copy it came from: { mode, label, fidelity, fidelityLabel,
 * runLocation, cloneName?, build }.
 */
export const replicationRecordOf = (agent, env, build = 1) => {
  const r = replicationOf(agent, env);
  return {
    mode: r.mode,
    label: r.label,
    fidelity: r.fidelity,
    fidelityLabel: r.fidelityLabel,
    proofKind: r.proofKind,
    proofLabel: r.proofLabel,
    ...(r.runLocation && { runLocation: r.runLocation }),
    ...(r.mode === "provider_clone" && { cloneName: cloneNameFor(env, build) }),
    build,
  };
};
