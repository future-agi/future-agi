import { parseGitHubInput, parseEgressDomains } from "src/pages/dashboard/harness/requestMapper";

/**
 * Maps a redacted Phase-1 source draft to a schema-valid `HarnessPreflight`
 * body (or `{ skipped: reason }` when the draft cannot be preflighted for
 * real). Contract: `HarnessSource.kind ∈ {github, archive, remote, provider}`,
 * `HarnessAgent.connector ∈ {livekit, vapi, retell, retell_chat, auto}`
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
};

// Only the Phase-1 roster providers that exist in the HarnessAgent.connector
// enum. `retell_chat` is in the enum but unreachable from the Phase-1 rosters.
export const PROVIDER_TO_CONNECTOR = {
  vapi: PREFLIGHT_CONNECTOR.VAPI,
  retell: PREFLIGHT_CONNECTOR.RETELL,
  livekit: PREFLIGHT_CONNECTOR.LIVEKIT,
};

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
  if (draft.kind === "platform") return draft.agentId || "agent";
  if (draft.kind === "upload") return draft.entry || draft.files?.[0]?.name || "agent";
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
    scenario_count: SCENARIO_COUNT,
    artifacts: { ...ARTIFACTS },
    metadata: { name, authoring_key: name },
  };
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
      // `auto` still lets the backend detect the connector from the source; the
      // exchanged credential refs ride along so a detected provider's
      // `credentials_present` check can pass.
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

  const idKey = connector === PREFLIGHT_CONNECTOR.VAPI ? "assistant_id" : "agent_id";
  return {
    payload: {
      ...envelope(draft, name),
      agent: {
        connector,
        mode: "connect_only",
        config: { [idKey]: draft.agentId },
        // The plaintext key never reaches here — the handoff exchanges it for an
        // opaque `{alias: reference}` map before the draft is persisted. Absent
        // (a provider with no single-key exchange), the schema default applies.
        secret_refs: draft.secret_refs || {},
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
