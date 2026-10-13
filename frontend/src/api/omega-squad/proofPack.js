/**
 * Offline verification for the Omega proof-pack contract.
 *
 * REA keeps evidence provider-neutral, self-describing, and independently
 * verifiable. Squad applies the same boundary to the remote Omega proof pack:
 * the browser may render a pack only after checking its protocol, task
 * identity, content-addressed artifacts, and canonical integrity seal. This
 * module performs no network or storage lookup.
 */

export const OMEGA_PROOF_PACK_PROTOCOL = "omega-proof-pack/v1";
export const OMEGA_PROOF_PACK_RUN_EVIDENCE_PROTOCOL =
  "omega-proof-pack-run-evidence/v1";
export const OMEGA_RUN_EVIDENCE_ISOLATION_PROTOCOL =
  "omega-managed-agent-run-isolation-receipt/v1";

const SHA256_HEX = /^[0-9a-f]{64}$/;
const TRANSPORT_REQUEST_ID = /^req_[0-9a-f]{32}$/;
const REMOTE_ISOLATION_PROVIDERS = new Set([
  "container",
  "docker",
  "microvm",
  "daytona",
  "e2b",
  "modal",
  "runloop",
  "remote",
]);
const SEALED_TOP_LEVEL_KEYS = new Set([
  "protocol",
  "generatedAt",
  "task",
  "answer",
  "spend",
  "receipts",
  "artifacts",
  "citation",
  "reconciliation",
  "embedMaxBytes",
  "runEvidence",
]);

/**
 * @typedef {Object} ProofPackUnavailable
 * @property {false} available
 * @property {string} code
 * @property {string} reason
 */

/**
 * @typedef {Object} ProofPackViolation
 * @property {string} code
 * @property {string} detail
 * @property {string} [ref]
 */

const unavailable = (code, reason) => ({ available: false, code, reason });

const isRecord = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const isNonEmptyString = (value) =>
  typeof value === "string" && value.trim().length > 0;

const addViolation = (violations, code, detail, ref) => {
  violations.push({ code, detail, ...(ref === undefined ? {} : { ref }) });
};

/**
 * Parse the stable parts of an Omega proof pack without claiming that its
 * integrity seal is valid. Unknown top-level fields are retained for the
 * verifier to report as a sealed-contract violation.
 *
 * @param {unknown} value
 * @param {{taskId?: string}} [options]
 * @returns {{available: true, proofPack: Object} | ProofPackUnavailable}
 */
export function parseConversationProofPack(value, options = {}) {
  if (!isRecord(value))
    return unavailable("MALFORMED", "The remote proof pack is not an object.");
  if (value.protocol !== OMEGA_PROOF_PACK_PROTOCOL) {
    return unavailable(
      "MALFORMED",
      "The remote proof pack has an unsupported protocol.",
    );
  }
  if (
    !isRecord(value.integrity) ||
    value.integrity.algorithm !== "sha256" ||
    !SHA256_HEX.test(value.integrity.hash || "")
  ) {
    return unavailable(
      "MALFORMED",
      "The remote proof pack has no valid SHA-256 integrity seal.",
    );
  }
  if (!isRecord(value.task) || !isNonEmptyString(value.task.id)) {
    return unavailable(
      "MALFORMED",
      "The remote proof pack has no task identity.",
    );
  }
  if (options.taskId !== undefined && value.task.id !== options.taskId) {
    return unavailable(
      "TASK_MISMATCH",
      "The remote proof pack belongs to a different conversation.",
    );
  }
  if (
    typeof value.answer !== "string" ||
    !isNonNegativeInteger(value.embedMaxBytes)
  ) {
    return unavailable(
      "MALFORMED",
      "The remote proof pack has an invalid answer or embed limit.",
    );
  }
  if (
    !isRecord(value.receipts) ||
    !Array.isArray(value.receipts.groundTruth) ||
    !Array.isArray(value.receipts.promotionRefs)
  ) {
    return unavailable(
      "MALFORMED",
      "The remote proof pack has an invalid receipt envelope.",
    );
  }
  if (!Array.isArray(value.artifacts)) {
    return unavailable(
      "MALFORMED",
      "The remote proof pack has an invalid artifact list.",
    );
  }
  for (const [index, artifact] of value.artifacts.entries()) {
    const reason = validateArtifact(artifact);
    if (reason !== null)
      return unavailable(
        "MALFORMED",
        `The remote proof pack artifact ${index} ${reason}.`,
      );
  }
  if (value.request_id !== undefined && typeof value.request_id !== "string") {
    return unavailable(
      "MALFORMED",
      "The remote proof pack request id is not a string.",
    );
  }
  return { available: true, proofPack: value };
}

/**
 * Verify an Omega proof pack offline. The result is intentionally a normal
 * value: tampering and incomplete evidence are expected proof outcomes, not
 * exceptional control flow. A digest implementation is the only capability
 * required; no remote request is made.
 *
 * @param {unknown} value
 * @param {{taskId?: string}} [options]
 * @returns {Promise<{available: true, ok: boolean, checkedArtifacts: number, embeddedArtifacts: number, isolation: {status: "unknown" | "verified" | "invalid", receipt?: Object, violations: ProofPackViolation[]}, violations: ProofPackViolation[]} | ProofPackUnavailable>}
 */
export async function verifyConversationProofPack(value, options = {}) {
  const parsed = parseConversationProofPack(value, options);
  if (!parsed.available) return parsed;
  const pack = parsed.proofPack;
  const violations = [];

  for (const key of Object.keys(pack)) {
    if (
      key !== "integrity" &&
      key !== "request_id" &&
      !SEALED_TOP_LEVEL_KEYS.has(key)
    ) {
      addViolation(
        violations,
        "unknown_top_level_field",
        `unrecognized proof-pack field ${key}`,
        key,
      );
    }
  }

  if (
    pack.request_id !== undefined &&
    !TRANSPORT_REQUEST_ID.test(pack.request_id)
  ) {
    addViolation(
      violations,
      "invalid_transport_request_id",
      "request_id is not a daemon transport id",
      "request_id",
    );
  }

  const digestAvailable =
    typeof globalThis.crypto?.subtle?.digest === "function";
  if (!digestAvailable)
    return unavailable(
      "DIGEST_UNAVAILABLE",
      "This browser cannot verify SHA-256 proof-pack seals.",
    );

  const { integrity, request_id: requestId, ...unsignedBody } = pack;
  const body =
    requestId === undefined || TRANSPORT_REQUEST_ID.test(requestId)
      ? unsignedBody
      : { ...unsignedBody, request_id: requestId };
  const recomputed = await hashCanonicalJson(body);
  if (integrity.hash !== recomputed) {
    addViolation(
      violations,
      "integrity_mismatch",
      `bundle hash ${integrity.hash} != recomputed ${recomputed}`,
    );
  }

  let embeddedArtifacts = 0;
  for (const artifact of pack.artifacts) {
    if (artifact.storage.embedded !== true) continue;
    embeddedArtifacts += 1;
    const bytes = decodeBase64(artifact.storage.content);
    if (bytes === null) {
      addViolation(
        violations,
        "artifact_content_invalid",
        `embedded artifact ${artifact.name} is not canonical base64`,
        artifact.name,
      );
      continue;
    }
    if (bytes.byteLength !== artifact.bytes) {
      addViolation(
        violations,
        "artifact_size_mismatch",
        `artifact ${artifact.name}: decoded ${bytes.byteLength} bytes != declared ${artifact.bytes}`,
        artifact.name,
      );
    }
    const artifactDigest = await sha256Hex(bytes);
    if (artifactDigest !== artifact.sha256) {
      addViolation(
        violations,
        "artifact_hash_mismatch",
        `artifact ${artifact.name}: content hashes to ${artifactDigest} != declared ${artifact.sha256}`,
        artifact.name,
      );
    }
  }

  const isolation = await verifyRunEvidenceLink(pack, violations);
  return {
    available: true,
    ok: violations.length === 0,
    checkedArtifacts: pack.artifacts.length,
    embeddedArtifacts,
    isolation,
    violations,
  };
}

/**
 * Verify the server-projected per-run OCI isolation receipt. REA treats
 * provider/readiness and cleanup as evidence dimensions with explicit unknowns;
 * Squad follows that rule instead of inferring isolation from a provider label
 * or a successful HTTP response. Missing receipts are therefore `unknown`,
 * while malformed or unobserved receipts are `invalid` and make the enclosing
 * proof pack unverifiable.
 *
 * @param {unknown} value
 * @param {{runId?: string}} [options]
 * @returns {{status: "unknown" | "verified" | "invalid", receipt?: Object, violations: ProofPackViolation[]}}
 */
export function verifyConversationIsolationReceipt(value, options = {}) {
  if (value === undefined) return { status: "unknown", violations: [] };
  const violations = [];
  const receipt = isRecord(value) ? value : null;
  const invalid = (code, detail, ref) =>
    addViolation(violations, code, detail, ref);

  if (!receipt) {
    invalid(
      "isolation_receipt_invalid",
      "isolationReceipt is not an object",
      "isolationReceipt",
    );
    return { status: "invalid", violations };
  }
  if (receipt.schemaVersion !== OMEGA_RUN_EVIDENCE_ISOLATION_PROTOCOL) {
    invalid(
      "isolation_receipt_invalid",
      "isolationReceipt has an unsupported schema version",
      "isolationReceipt.schemaVersion",
    );
  }
  if (options.runId !== undefined && receipt.runId !== options.runId) {
    invalid(
      "isolation_receipt_run_mismatch",
      "isolationReceipt belongs to a different conversation",
      "isolationReceipt.runId",
    );
  }
  if (!isNonEmptyString(receipt.planId))
    invalid(
      "isolation_receipt_invalid",
      "isolationReceipt has no benchmark plan binding",
      "isolationReceipt.planId",
    );
  if (!REMOTE_ISOLATION_PROVIDERS.has(receipt.provider)) {
    invalid(
      "isolation_provider_unverified",
      "isolationReceipt does not identify a supported remote sandbox provider",
      "isolationReceipt.provider",
    );
  }
  if (receipt.isolationClass !== "oci_container")
    invalid(
      "isolation_receipt_invalid",
      "isolationReceipt does not prove OCI container isolation",
      "isolationReceipt.isolationClass",
    );
  if (!isNonEmptyString(receipt.instanceId))
    invalid(
      "isolation_receipt_invalid",
      "isolationReceipt has no instance identity",
      "isolationReceipt.instanceId",
    );
  if (!/^sha256:[0-9a-f]{64}$/.test(receipt.imageDigest || ""))
    invalid(
      "isolation_receipt_invalid",
      "isolationReceipt has no valid image digest",
      "isolationReceipt.imageDigest",
    );
  if (!SHA256_HEX.test(receipt.resourceLimitsSha256 || ""))
    invalid(
      "isolation_receipt_invalid",
      "isolationReceipt has no valid resource-limits digest",
      "isolationReceipt.resourceLimitsSha256",
    );
  if (
    receipt.bootEvidenceStatus !== "observed" ||
    receipt.bootAttempted !== true ||
    receipt.bootSucceeded !== true
  ) {
    invalid(
      "isolation_receipt_unobserved",
      "isolationReceipt does not contain observed successful boot evidence",
      "isolationReceipt.bootEvidenceStatus",
    );
  }
  if (!new Set(["removed", "destroyed"]).has(receipt.cleanupStatus)) {
    invalid(
      "isolation_receipt_cleanup_unverified",
      "isolationReceipt does not prove cleanup completed",
      "isolationReceipt.cleanupStatus",
    );
  }
  const timestamps = [
    "createdAt",
    "startedAt",
    "stoppedAt",
    "cleanupCompletedAt",
  ];
  const parsedTimes = timestamps.map((key) => {
    const valueAtKey = receipt[key];
    if (!isCanonicalIsolationTimestamp(valueAtKey)) {
      invalid(
        "isolation_receipt_invalid",
        `${key} is not a canonical ISO timestamp`,
        `isolationReceipt.${key}`,
      );
      return null;
    }
    return Date.parse(valueAtKey);
  });
  if (parsedTimes.every((time) => time !== null)) {
    for (let index = 1; index < parsedTimes.length; index += 1) {
      if (parsedTimes[index] < parsedTimes[index - 1]) {
        invalid(
          "isolation_receipt_timeline_invalid",
          "isolationReceipt lifecycle timestamps are out of order",
          "isolationReceipt",
        );
        break;
      }
    }
  }
  return {
    status: violations.length === 0 ? "verified" : "invalid",
    receipt,
    violations,
  };
}

/**
 * Canonical SHA-256 used by Omega's proof-pack verifier. Exported for callers
 * that need to display the expected seal; it does not trust a server value.
 *
 * @param {unknown} value
 * @returns {Promise<string>}
 */
export async function hashCanonicalJson(value) {
  return sha256Hex(
    new TextEncoder().encode(JSON.stringify(canonicalize(value))),
  );
}

function validateArtifact(artifact) {
  if (
    !isRecord(artifact) ||
    !isNonEmptyString(artifact.name) ||
    !SHA256_HEX.test(artifact.sha256 || "") ||
    !isNonNegativeInteger(artifact.bytes)
  ) {
    return "does not contain a valid name, SHA-256, and byte count";
  }
  if (!isRecord(artifact.storage)) return "has no storage descriptor";
  if (artifact.storage.embedded === true) {
    if (
      artifact.storage.encoding !== "base64" ||
      typeof artifact.storage.content !== "string"
    )
      return "has an invalid embedded storage descriptor";
    return null;
  }
  if (
    artifact.storage.embedded === false &&
    !["oversize", "content_unavailable"].includes(artifact.storage.reason)
  ) {
    return "has an invalid referenced storage descriptor";
  }
  return artifact.storage.embedded === false
    ? null
    : "has an invalid storage descriptor";
}

async function verifyRunEvidenceLink(pack, violations) {
  const link = pack.runEvidence;
  if (link === undefined) return { status: "unknown", violations: [] };
  if (
    !isRecord(link) ||
    link.schemaVersion !== OMEGA_PROOF_PACK_RUN_EVIDENCE_PROTOCOL ||
    !isRecord(link.integrity) ||
    link.integrity.algorithm !== "sha256" ||
    !SHA256_HEX.test(link.integrity.hash || "") ||
    !isNonEmptyString(link.manifestHash) ||
    !isNonEmptyString(link.taskTreeHash) ||
    !isRecord(link.manifest)
  ) {
    addViolation(
      violations,
      "run_evidence_invalid",
      "runEvidence is not a sealed Omega run-evidence link",
      "runEvidence",
    );
    return { status: "invalid", violations: [] };
  }
  const { integrity, ...linkBody } = link;
  const linkDigest = await hashCanonicalJson(linkBody);
  if (integrity.hash !== linkDigest) {
    addViolation(
      violations,
      "run_evidence_integrity_mismatch",
      `runEvidence hash ${integrity.hash} != recomputed ${linkDigest}`,
      "runEvidence",
    );
  }
  if (linkBody.manifest.runId !== pack.task.id) {
    addViolation(
      violations,
      "run_evidence_task_mismatch",
      "runEvidence is bound to a different task",
      "runEvidence.manifest.runId",
    );
  }
  if (
    !isRecord(link.manifest.integrity) ||
    link.manifest.integrity.algorithm !== "sha256" ||
    !SHA256_HEX.test(link.manifest.integrity.hash || "")
  ) {
    addViolation(
      violations,
      "run_evidence_invalid",
      "runEvidence manifest has no valid integrity seal",
      "runEvidence.manifest",
    );
    return { status: "invalid", violations: [] };
  }
  const manifestIntegrity = link.manifest.integrity;
  const manifestBody = Object.fromEntries(
    Object.entries(link.manifest).filter(
      ([key]) => key !== "integrity" && key !== "request_id",
    ),
  );
  const manifestDigest = await hashCanonicalJson(manifestBody);
  if (manifestIntegrity.hash !== manifestDigest) {
    addViolation(
      violations,
      "run_evidence_integrity_mismatch",
      `manifest hash ${manifestIntegrity.hash} != recomputed ${manifestDigest}`,
      "runEvidence.manifest",
    );
  }
  if (!isRecord(link.manifest.taskTree)) {
    addViolation(
      violations,
      "run_evidence_invalid",
      "runEvidence manifest has no task-tree seal",
      "runEvidence.taskTree",
    );
    return { status: "invalid", violations: [] };
  }
  const taskTree = link.manifest.taskTree;
  if (
    !isRecord(taskTree.integrity) ||
    taskTree.integrity.algorithm !== "sha256" ||
    !SHA256_HEX.test(taskTree.integrity.hash || "")
  ) {
    addViolation(
      violations,
      "run_evidence_invalid",
      "runEvidence task-tree has no valid integrity seal",
      "runEvidence.taskTree",
    );
    return { status: "invalid", violations: [] };
  }
  const { integrity: taskTreeIntegrity, ...taskTreeBody } = taskTree;
  const taskTreeDigest = await hashCanonicalJson(taskTreeBody);
  if (
    taskTreeIntegrity.hash !== taskTreeDigest ||
    link.taskTreeHash !== taskTreeIntegrity.hash
  ) {
    addViolation(
      violations,
      "run_evidence_integrity_mismatch",
      "runEvidence task-tree hash does not match its sealed task tree",
      "runEvidence.taskTree",
    );
  }
  if (link.manifestHash !== manifestIntegrity.hash) {
    addViolation(
      violations,
      "run_evidence_integrity_mismatch",
      "runEvidence manifest hash does not match its sealed manifest",
      "runEvidence.manifestHash",
    );
  }
  const isolation = verifyConversationIsolationReceipt(
    link.manifest.isolationReceipt,
    { runId: pack.task.id },
  );
  for (const violation of isolation.violations) {
    addViolation(violations, violation.code, violation.detail, violation.ref);
  }
  return isolation;
}

function isCanonicalIsolationTimestamp(value) {
  if (typeof value !== "string" || value.length !== 24) return false;
  const parsed = Date.parse(value);
  return (
    Number.isFinite(parsed) &&
    new Date(parsed).toISOString() === value &&
    parsed >= Date.parse("2000-01-01T00:00:00.000Z") &&
    parsed <= Date.parse("2100-01-01T00:00:00.000Z")
  );
}

function isNonNegativeInteger(value) {
  return Number.isInteger(value) && value >= 0;
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

async function sha256Hex(value) {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", value);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function decodeBase64(value) {
  if (
    typeof value !== "string" ||
    value.length % 4 !== 0 ||
    !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
      value,
    )
  )
    return null;
  try {
    const binary = atob(value);
    const bytes = Uint8Array.from(binary, (character) =>
      character.charCodeAt(0),
    );
    if (encodeBase64(bytes) !== value) return null;
    return bytes;
  } catch {
    return null;
  }
}

function encodeBase64(bytes) {
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}
