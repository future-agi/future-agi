export const ID = "10000000-0000-4000-8000-000000000001";
export const VERSION_ID = "20000000-0000-4000-8000-000000000002";
export const SOURCE_ID = "30000000-0000-4000-8000-000000000003";
export const DIGEST = `sha256:${"a".repeat(64)}`;
export const CONFIG = `sha256:${"b".repeat(64)}`;
export const textEvidence = {
  schema: 1,
  source: "skills_text",
  sourceId: SOURCE_ID,
  subjectKind: "agent_context",
  subjectId: ID,
  candidateDigest: DIGEST,
  executionFingerprint: DIGEST,
  status: "completed",
  visibility: "workspace",
  payloadExpiresAt: "2099-12-01T00:00:00Z",
  annotationRevision: 1,
  configuration: {
    schema: 1,
    digest: CONFIG,
    text: {
      model: { modelId: "fixture-model", temperature: 0, maxOutputTokens: 128 },
      judge: null,
      harnessVersion: "contexts-text-v1.2",
      adapterVersion: "fixture-adapter",
      evaluatorVersion: "fixture-evaluator",
    },
  },
};
export const nativeEvidence = {
  ...textEvidence,
  source: "simulation_native",
  sourceId: "40000000-0000-4000-8000-000000000004",
  configuration: {
    schema: 1,
    digest: CONFIG,
    native: {
      codeCommit: "c".repeat(40),
      entrypoint: "fixture:run",
      runtimeVersion: "fixture-runtime",
      toolsDigest: DIGEST,
    },
  },
};
export const packet = {
  schema: 1,
  subjectId: ID,
  subjectKind: "agent_context",
  candidateVersionId: VERSION_ID,
  candidateDigest: DIGEST,
  transition: {
    label: "production",
    expectedVersion: 4,
    version: 7,
    expectedReleaseRevision: 3,
    expectedSecurityRevision: 0,
    expectedImpactFingerprint: DIGEST,
  },
  target: {
    bindingId: ID,
    environmentId: null,
    revision: 2,
    configurationDigest: CONFIG,
  },
  evidence: [textEvidence, nativeEvidence],
  externalPipeline: {
    trust: "operator_recorded_unimported",
    url: "https://example.invalid/run/42",
    configurationIdentity: "fixture-ci-commit",
    decision: "hold",
    recordedBy: ID,
  },
};
export const preview = { packet, fingerprint: DIGEST, current: true };
export const request = { id: ID, revision: 2, status: "pending" };
export const changes = [
  {
    path: "references/policy.md",
    status: "modified",
    fromText: "Before\n",
    toText: "After\n",
  },
  {
    path: "AGENTS.md",
    status: "modified",
    fromText: "# Policy\nUse thirty days.\n",
    toText: "# Policy\nUse fourteen days.\n",
  },
];
export const capability = {
  beforeDigest: DIGEST,
  afterDigest: DIGEST,
  changes: [
    {
      path: "scripts/check.py",
      category: "executable",
      name: null,
      change: "added",
      before: null,
      after: "fixture hash",
    },
  ],
};
export const draftReview = {
  subjectId: ID,
  revision: 8,
  baseVersion: 4,
  expectedHeadVersion: 6,
  headDigest: CONFIG,
  candidateDigest: DIGEST,
  reviewFingerprint: DIGEST,
  productionVersion: 4,
  current: true,
  complete: true,
  headAcknowledged: true,
  changes,
  headChanges: changes,
  acceptedLock: { links: [] },
  checks: {
    items: [],
    summary: { errors: 0, warnings: 0, infos: 0 },
    tokens: {},
  },
  capabilities: capability,
};
export const inbox = {
  results: [
    {
      id: ID,
      subjectId: ID,
      subjectKind: "agent_context",
      name: "refund-context",
      authorId: SOURCE_ID,
      revision: 2,
      status: "pending",
      unread: true,
      fingerprint: DIGEST,
      candidateVersionId: VERSION_ID,
    },
  ],
  count: 1,
  unreadCount: 1,
  page: 1,
  pageSize: 25,
  totalPages: 1,
};
export const capture = {
  captureId: ID,
  sourceFingerprint: DIGEST,
  expiresAt: "2099-12-01T00:00:00Z",
  recordedContext: { versionId: VERSION_ID, digest: DIGEST },
  segments: [
    {
      id: "request-1",
      kind: "request",
      text: "Customer private-address asks for refund.",
    },
    { id: "tool-1", kind: "tool_result", text: "Purchased fourteen days ago." },
  ],
  omitted: [{ reason: "Hidden reasoning excluded" }],
  outboundFindings: [],
};
export const policy = {
  revision: 2,
  enabled: false,
  periodLimitUsd: null,
  startsAt: "2026-11-01T00:00:00Z",
  endsAt: "2026-12-01T00:00:00Z",
  remainingUsd: null,
  knownEstimateUsd: "2.12000000",
  committedUsd: "3.12000000",
  unknownReservedUsd: "1.00000000",
  priorPeriodUnknownUsd: "0.50000000",
};
export const snapshot = {
  kind: "agent_context",
  name: "refund-context",
  resourceId: ID,
  versionId: VERSION_ID,
  version: 7,
  digest: DIGEST,
  requested: { label: "production" },
};
export const qualification = {
  released: true,
  packageVersion: "0.0.1",
  profiles: [
    {
      id: "codex",
      version: "fixture-profile",
      state: "verified",
      hostVersions: ["fixture-host"],
    },
  ],
};
