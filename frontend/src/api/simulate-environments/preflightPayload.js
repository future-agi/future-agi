import { parseGitHubInput, parseEgressDomains } from "src/pages/dashboard/harness/requestMapper";

/**
 * Maps a redacted Phase-1 source draft to a schema-valid `HarnessPreflight`
 * body (or `{ skipped: reason }` when the draft cannot be preflighted for
 * real). Contract: `HarnessSource.kind ∈ {github, archive, remote, provider}`,
 * `HarnessAgent.connector ∈ {livekit, vapi, retell, retell_chat, phone, auto}`
 * (openapi-contract.generated.js HarnessSource / HarnessAgent). The body is
 * runtime-validated client-side, so undefined keys are never emitted.
 *
 * This module imports nothing from `sections/`; `DEFAULT_BRANCH` is duplicated
 * here rather than pulled from the panel constants for that reason.
 */

// Fixed envelope every preflight body carries, regardless of source kind:
// the payload schema version, the default git branch when the draft names none,
// how many scenarios to plan for, and the artifact-retention block.
const SCHEMA_VERSION = "futureagi.harness-job.v1";
const DEFAULT_BRANCH = "main";
const SCENARIO_COUNT = 10;
// The backend's `runtime.cpu_units`, which this payload never sends and which
// therefore takes HarnessRuntimeSerializer's default of 4. Above it the request
// is rejected as "voice parallelism must not exceed cpu_units" for every
// connector this module emits, `auto` (repo and upload) included. Duplicated
// from sections/parallelism.constants.js for the no-sections-imports rule in the
// module docstring above — keep the two in step.
const MAX_PARALLELISM = 4;

const ARTIFACTS = {
  level: "full",
  retention_days: 30,
  allow_bundle_download: false,
  max_artifact_bytes: 1073741824,
};

export const PREFLIGHT_CONNECTOR = {
  AUTO: "auto",
  VAPI: "vapi",
  RETELL: "retell",
  RETELL_CHAT: "retell_chat",
  LIVEKIT: "livekit",
  PHONE: "phone",
};

// The roster providers that exist in the HarnessAgent.connector enum. `retell`
// is the voice connector; `retell_chat` is the only real chat connector.
// "Others" is an agent that answers a phone number: the platform dials it and
// the pasted system prompt stands in for source code.
export const PROVIDER_TO_CONNECTOR = {
  vapi: PREFLIGHT_CONNECTOR.VAPI,
  retell: PREFLIGHT_CONNECTOR.RETELL,
  retell_chat: PREFLIGHT_CONNECTOR.RETELL_CHAT,
  livekit: PREFLIGHT_CONNECTOR.LIVEKIT,
  other: PREFLIGHT_CONNECTOR.PHONE,
};

// The contact panel keeps the dial code and the local number apart; the
// backend wants one E.164 string. Empty when the draft has no usable number.
//
// Treat the number as already-international only when it is written that way — a
// leading `+`. Never infer a prefix from the national digits: a national number
// whose leading digits coincide with the dial code (India +91, 9123456789) would
// otherwise be sent as +9123456789 instead of +919123456789.
//
// A national number written with its trunk prefix (UK 07911 123456) drops that
// one leading 0 once the dial code goes on, or it dials +4407911… — a number
// that doesn't exist. Italy keeps its 0 in international form.
const KEEPS_TRUNK_ZERO = new Set(["39"]);

function e164(contact) {
  const dial = String(contact?.countryCode || "").replace(/\D/g, "");
  const raw = String(contact?.number || "").trim();
  const local = raw.replace(/\D/g, "");
  if (!local) return "";
  if (raw.startsWith("+")) return `+${local}`;
  if (!dial) return `+${local}`;
  const national = local.startsWith("0") && !KEEPS_TRUNK_ZERO.has(dial) ? local.slice(1) : local;
  return `+${dial}${national}`;
}

// Who calls whom and who opens, as the backend's two config booleans. Only
// emitted when the panel collected them, so a draft without contact details
// keeps the schema defaults.
function callBehaviour(contact) {
  if (!contact) return {};
  return {
    inbound: contact.inboundCalls !== false,
    target_speaks_first: Boolean(contact.agentSpeaksFirst),
  };
}

const lastSegment = (value) =>
  (value || "")
    .replace(/\/+$/, "")
    .split("/")
    .filter(Boolean)
    .at(-1) || "";

export function environmentNameFor(draft) {
  if (!draft) return "agent";
  if (draft.kind === "repo") {
    const repository = parseGitHubInput(draft.value)?.repository || draft.value;
    return lastSegment(repository) || "agent";
  }
  if (draft.kind === "platform") {
    return draft.agentId || (draft.agentMode === "prompt" ? "phone-agent" : "agent");
  }
  // An upload is a folder, so name it after the folder — not the entry path or
  // some file inside it (which produced names like "requirements.txt").
  if (draft.kind === "upload") {
    return draft.folderName || draft.entry || draft.files?.[0]?.name || "agent";
  }
  return "agent";
}

// parsed.ref (from a pasted tree URL) beats the panel's default "main"; an
// explicitly typed non-default branch beats both. Empty on both sides → omit.
function resolveRef(parsedRef, draftRef) {
  const typed = (draftRef || "").trim();
  if (!typed || typed === DEFAULT_BRANCH) return parsedRef || typed || undefined;
  return typed;
}

// Common envelope. `security` is emitted only when egress yields ≥1 public
// domain — otherwise the schema default applies.
function envelope(draft, name) {
  const payload = {
    schema_version: SCHEMA_VERSION,
    // The count the user chose in the build form (validated 1–500); falls back
    // to the default when a draft predates the field.
    scenario_count: draft.scenarioCount ?? SCENARIO_COUNT,
    artifacts: { ...ARTIFACTS },
    metadata: { name, authoring_key: name },
  };
  const parallelism = Math.min(
    MAX_PARALLELISM,
    Math.max(1, Math.trunc(Number(draft.parallelism) || 1)),
  );
  if (parallelism > 1) payload.runtime = { parallelism };
  const domains = parseEgressDomains(draft.egress);
  if (domains.length) {
    payload.security = {
      untrusted_source: true,
      read_only_source: true,
      allow_privileged: false,
      allow_host_runtime_control: false,
      allowed_egress_domains: domains,
    };
  }
  return payload;
}

function repoPayload(draft, name) {
  const parsed = parseGitHubInput(draft.value);
  if (!parsed) return { skipped: "Repository must be owner/repo" };

  const source = {
    kind: "github",
    repository: parsed.repository,
    visibility: draft.visibility || "public",
  };
  const ref = resolveRef(parsed.ref, draft.ref);
  if (ref) source.ref = ref;
  if (draft.visibility === "private" && String(draft.installationId || "").trim()) {
    source.installation_id = String(draft.installationId).trim();
  }

  return {
    payload: {
      ...envelope(draft, name),
      source,
      // Source detection uses these refs to report credential readiness.
      agent: {
        connector: PREFLIGHT_CONNECTOR.AUTO,
        config: {},
        secret_refs: draft.secret_refs || {},
      },
    },
  };
}

function platformPayload(draft, name) {
  const connector = PROVIDER_TO_CONNECTOR[draft.provider];
  if (!connector) return { skipped: `\`${draft.provider}\` is not a preflight connector yet` };

  const contact = draft.contact;
  const usesPhone = connector === PREFLIGHT_CONNECTOR.PHONE || contact?.mode === "phone";
  const phoneNumber = usesPhone ? e164(contact) : "";
  if (usesPhone && !phoneNumber) {
    return { skipped: "A phone call needs the agent's number" };
  }

  let config;
  if (connector === PREFLIGHT_CONNECTOR.PHONE) {
    const prompt = String(draft.prompt || "").trim();
    if (!prompt) return { skipped: "Others needs the agent's system prompt" };
    // The platform dials the number itself, so the caller can only be the
    // simulator: inbound is fixed, only who opens is a choice.
    config = {
      phone_number: phoneNumber,
      target_system_prompt: prompt,
      ...(contact ? { inbound: true, target_speaks_first: Boolean(contact.agentSpeaksFirst) } : {}),
    };
  } else {
    const idKey = connector === PREFLIGHT_CONNECTOR.VAPI ? "assistant_id" : "agent_id";
    config = {
      [idKey]: draft.agentId,
      ...(phoneNumber ? { phone_number: phoneNumber } : {}),
      ...callBehaviour(contact),
    };
  }
  // Others is always inbound (the platform dials the number), so it sends that
  // direction rather than whatever the toggle last held.
  const callDirection =
    connector === PREFLIGHT_CONNECTOR.PHONE ? "inbound" : draft.callDirection;
  return {
    payload: {
      ...envelope(draft, name),
      agent: {
        connector,
        mode: "connect_only",
        config,
        // The plaintext key never reaches here — the handoff exchanges it for an
        // opaque `{alias: reference}` map before the draft is persisted. Absent
        // (a provider with no single-key exchange), the schema default applies.
        secret_refs: draft.secret_refs || {},
        // The direction as the panel collected it. The backend honours
        // `config.inbound` first and this second, before the authored guess.
        ...(callDirection ? { call_direction: callDirection } : {}),
      },
    },
  };
}

function uploadPayload(draft, name) {
  if (!draft.archive_artifact_id) {
    return { skipped: "Code upload preflight needs the uploaded archive" };
  }
  return {
    payload: {
      ...envelope(draft, name),
      source: { kind: "archive", archive_artifact_id: draft.archive_artifact_id },
      // Source detection uses these refs to report credential readiness.
      agent: {
        connector: PREFLIGHT_CONNECTOR.AUTO,
        config: {},
        secret_refs: draft.secret_refs || {},
      },
    },
  };
}

export function draftToPreflightPayload(draft) {
  if (!draft) return { skipped: "No source to preflight" };
  const name = environmentNameFor(draft);
  if (draft.kind === "repo") return repoPayload(draft, name);
  if (draft.kind === "platform") return platformPayload(draft, name);
  if (draft.kind === "upload") return uploadPayload(draft, name);
  return { skipped: `\`${draft.kind}\` is not a preflightable source` };
}
