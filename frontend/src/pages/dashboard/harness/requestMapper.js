/**
 * Maps the Harness UI state to the nested futureagi.harness-job.v1 request
 * schema accepted by the platform's HarnessJobCreateSerializer.
 *
 * The frontend NEVER sends raw environment_values in the job payload.
 * Credential files uploaded via the /secret-files endpoint return
 * `harness_environment_file` refs which are incompatible with the v1.6 schema
 * (it only accepts `platform-vault` + `target_provider`). Until the backend
 * can materialize those, only platform-vault refs are forwarded.
 */

export const MAX_SCENARIO_COUNT = 200;

const GITHUB_URL_PATTERN =
  /^(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+?)(?:\/tree\/(.+?))?(?:\/)?$/;

const EGRESS_HOST_LABEL = "[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?";
const EGRESS_HOSTNAME_PATTERN = new RegExp(
  `^${EGRESS_HOST_LABEL}(?:\\.${EGRESS_HOST_LABEL})+$`,
);

const normalizeEgressDomain = (value) =>
  typeof value === "string"
    ? value.trim().toLowerCase().replace(/\.$/, "")
    : "";

const isPrivateOrReservedHost = (host) => {
  const unbracketedHost = host.replace(/^\[|\]$/g, "");
  return (
    unbracketedHost === "localhost" ||
    unbracketedHost === "host.docker.internal" ||
    unbracketedHost === "::1" ||
    unbracketedHost === "0:0:0:0:0:0:0:1" ||
    /^fe[89ab][0-9a-f]:/i.test(unbracketedHost) ||
    /^127(?:\.|$)/.test(unbracketedHost) ||
    unbracketedHost.startsWith("10.") ||
    unbracketedHost.startsWith("192.168.") ||
    unbracketedHost.startsWith("169.254.") ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(unbracketedHost)
  );
};

/**
 * Derive publicly routable hostnames from URL-valued environment/config values.
 * Non-URL values and localhost/private/link-local hosts are ignored.
 */
export function deriveEgressDomains(...maps) {
  const hosts = new Set();
  for (const map of maps) {
    for (const value of Object.values(map || {})) {
      if (typeof value !== "string") continue;
      const trimmed = value.trim();
      if (!/^(?:wss?|https?):\/\//i.test(trimmed)) continue;

      let host = "";
      try {
        host = normalizeEgressDomain(new URL(trimmed).hostname);
      } catch {
        continue;
      }
      if (!host || isPrivateOrReservedHost(host)) continue;
      hosts.add(host);
    }
  }
  return Array.from(hosts).sort();
}

/**
 * Parse comma/newline-separated customer egress hostnames.
 * Schemes, paths, and other obviously malformed values are ignored; backend validation remains
 * authoritative for private/reserved hosts and the resolved Daytona egress cap.
 */
export function parseEgressDomains(input) {
  if (typeof input !== "string") return [];

  const domains = new Set();
  for (const value of input.split(/[,\n]/)) {
    const domain = normalizeEgressDomain(value);
    if (!domain || domain.length > 253 || !EGRESS_HOSTNAME_PATTERN.test(domain)) continue;
    domains.add(domain);
  }
  return Array.from(domains).sort();
}

/**
 * Merge derived and customer-declared egress hostnames into a stable exact-deduped list.
 */
export function mergeEgressDomains(...domainLists) {
  const domains = new Set();
  for (const domainList of domainLists) {
    const values =
      typeof domainList === "string"
        ? parseEgressDomains(domainList)
        : domainList || [];
    for (const value of values) {
      const domain = normalizeEgressDomain(value);
      if (domain) domains.add(domain);
    }
  }
  return Array.from(domains).sort();
}

/**
 * Parse a GitHub repository input which may be:
 * - "owner/repo"
 * - "https://github.com/owner/repo"
 * - "https://github.com/owner/repo/tree/main"
 * - "https://github.com/owner/repo/tree/feature/branch"
 *
 * Returns { repository, ref } or null when unparseable.
 */
export function parseGitHubInput(input) {
  const trimmed = (input || "").trim();
  if (!trimmed) return null;

  // Direct owner/repo format
  if (/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(trimmed)) {
    return { repository: trimmed, ref: undefined };
  }

  const match = trimmed.match(GITHUB_URL_PATTERN);
  if (!match) return null;
  return {
    repository: match[1],
    ref: match[2] || undefined,
  };
}

/**
 * Filter secret_refs to only include platform-vault target_provider refs
 * that the v1.6 schema accepts.
 */
function filterSecretRefs(secretFileRefs) {
  const accepted = {};
  for (const [alias, ref] of Object.entries(secretFileRefs || {})) {
    if (ref?.manager === "platform-vault" && ref?.purpose === "target_provider") {
      accepted[alias] = ref;
    }
  }
  return accepted;
}

/**
 * Build the nested source object for the v1.6 payload.
 */
function buildSource({ sourceMode, uploadedSource, githubRepository, githubVisibility, githubInstallationId }) {
  if (sourceMode === "upload") {
    return {
      kind: "archive",
      archive_artifact_id: uploadedSource?.source_id || null,
      visibility: "public",
    };
  }

  const parsed = parseGitHubInput(githubRepository);
  if (!parsed) {
    return {
      kind: "github",
      repository: githubRepository.trim(),
      visibility: githubVisibility,
    };
  }

  const source = {
    kind: "github",
    repository: parsed.repository,
    visibility: githubVisibility,
  };
  if (parsed.ref) {
    source.ref = parsed.ref;
  }
  if (githubVisibility === "private" && githubInstallationId?.trim()) {
    source.installation_id = githubInstallationId.trim();
  }
  return source;
}

/**
 * Build the full futureagi.harness-job.v1 payload from UI state.
 *
 * @param {object} uiState
 * @param {string} uiState.sourceMode - "upload" | "github"
 * @param {object|null} uiState.uploadedSource - { source_id, ... }
 * @param {string} uiState.githubRepository - URL or owner/repo
 * @param {string} uiState.githubVisibility - "public" | "private"
 * @param {string} uiState.githubInstallationId
 * @param {number} uiState.scenarioCount
 * @param {object} uiState.configurationValues - non-secret config scalars
 * @param {object} uiState.secretFileRefs - refs from uploaded credential files
 * @returns {object} payload ready for POST to /harness-jobs/ or /harness-jobs/preflight/
 */
export function buildJobPayload({
  sourceMode,
  uploadedSource,
  githubRepository,
  githubVisibility,
  githubInstallationId,
  scenarioCount,
  configurationValues,
  secretFileRefs,
}) {
  const numericCount = Number(scenarioCount);
  const clampedCount = Math.max(
    1,
    Math.min(
      MAX_SCENARIO_COUNT,
      Number.isFinite(numericCount) ? Math.floor(numericCount) : 1,
    ),
  );

  return {
    schema_version: "futureagi.harness-job.v1",
    source: buildSource({
      sourceMode,
      uploadedSource,
      githubRepository,
      githubVisibility,
      githubInstallationId,
    }),
    agent: {
      connector: "auto",
      config: configurationValues || {},
      secret_refs: filterSecretRefs(secretFileRefs),
    },
    scenario_count: clampedCount,
    artifacts: {
      level: "traces-and-recordings",
    },
    metadata: {},
  };
}

/**
 * Return a list of unsupported credential warnings for the current UI state.
 * Each item is { name, reason } describing why the credential cannot be sent.
 *
 * The UI should show these warnings before allowing submission.
 */
export function unsupportedCredentialWarnings({ environmentValues, secretFileRefs }) {
  const warnings = [];

  // Raw environment values cannot be sent in the v1.6 payload
  const envNames = Object.keys(environmentValues || {}).filter(
    (name) => String(environmentValues[name]).trim() !== "",
  );
  if (envNames.length > 0) {
    warnings.push({
      names: envNames,
      reason:
        "Pasted environment values cannot be sent directly. Use platform vault secrets or upload credential files via the vault integration when available.",
    });
  }

  // harness_environment_file refs are incompatible with v1.6 schema
  for (const [alias, ref] of Object.entries(secretFileRefs || {})) {
    if (ref?.manager && ref.manager !== "platform-vault") {
      warnings.push({
        names: [alias],
        reason: `Credential file "${alias}" uses ${ref.manager} which is not yet supported in the job schema. Vault integration is needed to materialize this credential at runtime.`,
      });
    }
  }

  return warnings;
}
