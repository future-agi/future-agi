import { describe, expect, it } from "vitest";
import {
  OMEGA_RUN_EVIDENCE_ISOLATION_PROTOCOL,
  OMEGA_PROOF_PACK_PROTOCOL,
  hashCanonicalJson,
  parseConversationProofPack,
  verifyConversationIsolationReceipt,
  verifyConversationProofPack,
} from "./proofPack";

const artifactBytes = new TextEncoder().encode("hello");
const artifactSha256 =
  "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

async function sealedPack(overrides = {}) {
  const body = {
    protocol: OMEGA_PROOF_PACK_PROTOCOL,
    generatedAt: "2026-10-12T00:00:00.000Z",
    task: { id: "run-1", title: "Example" },
    answer: "completed",
    spend: { costUsd: 0.02, tokens: 12 },
    receipts: { groundTruth: [], promotionRefs: [] },
    artifacts: [
      {
        name: "answer.txt",
        sha256: artifactSha256,
        bytes: artifactBytes.byteLength,
        storage: { embedded: true, encoding: "base64", content: "aGVsbG8=" },
      },
    ],
    embedMaxBytes: 262144,
    ...overrides,
  };
  return {
    ...body,
    integrity: { algorithm: "sha256", hash: await hashCanonicalJson(body) },
  };
}

describe("Omega proof-pack contract", () => {
  it("accepts a sealed pack and verifies embedded content offline", async () => {
    const pack = await sealedPack();

    expect(parseConversationProofPack(pack, { taskId: "run-1" })).toMatchObject(
      { available: true },
    );
    await expect(
      verifyConversationProofPack(pack, { taskId: "run-1" }),
    ).resolves.toMatchObject({
      available: true,
      ok: true,
      checkedArtifacts: 1,
      embeddedArtifacts: 1,
      isolation: { status: "unknown", violations: [] },
      violations: [],
    });
  });

  it("verifies a server-projected OCI isolation receipt and keeps missing evidence explicit", () => {
    const receipt = {
      schemaVersion: OMEGA_RUN_EVIDENCE_ISOLATION_PROTOCOL,
      runId: "run-1",
      planId: "plan-1",
      provider: "docker",
      isolationClass: "oci_container",
      instanceId: "container-run-1",
      imageDigest: `sha256:${"a".repeat(64)}`,
      resourceLimitsSha256: "b".repeat(64),
      bootEvidenceStatus: "observed",
      bootAttempted: true,
      bootSucceeded: true,
      cleanupStatus: "destroyed",
      createdAt: "2026-10-12T00:00:00.000Z",
      startedAt: "2026-10-12T00:00:01.000Z",
      stoppedAt: "2026-10-12T00:00:02.000Z",
      cleanupCompletedAt: "2026-10-12T00:00:03.000Z",
    };

    expect(
      verifyConversationIsolationReceipt(undefined, { runId: "run-1" }),
    ).toMatchObject({
      status: "unknown",
      violations: [],
    });
    expect(
      verifyConversationIsolationReceipt(receipt, { runId: "run-1" }),
    ).toMatchObject({
      status: "verified",
      receipt,
      violations: [],
    });
  });

  it("rejects unobserved or unclean isolation receipts instead of inferring cloud isolation", () => {
    const result = verifyConversationIsolationReceipt(
      {
        schemaVersion: OMEGA_RUN_EVIDENCE_ISOLATION_PROTOCOL,
        runId: "run-1",
        planId: "plan-1",
        provider: "docker",
        isolationClass: "oci_container",
        instanceId: "container-run-1",
        imageDigest: `sha256:${"a".repeat(64)}`,
        resourceLimitsSha256: "b".repeat(64),
        bootEvidenceStatus: "unobserved",
        bootAttempted: true,
        bootSucceeded: false,
        cleanupStatus: "failed",
        createdAt: "2026-10-12T00:00:00.000Z",
        startedAt: "2026-10-12T00:00:01.000Z",
        stoppedAt: "2026-10-12T00:00:02.000Z",
        cleanupCompletedAt: "2026-10-12T00:00:03.000Z",
      },
      { runId: "run-1" },
    );

    expect(result.status).toBe("invalid");
    expect(result.violations.map(({ code }) => code)).toEqual(
      expect.arrayContaining([
        "isolation_receipt_unobserved",
        "isolation_receipt_cleanup_unverified",
      ]),
    );

    expect(
      verifyConversationIsolationReceipt(
        {
          ...result.receipt,
          provider: "local",
          bootEvidenceStatus: "observed",
          bootSucceeded: true,
          cleanupStatus: "destroyed",
        },
        { runId: "run-1" },
      ).violations.map(({ code }) => code),
    ).toContain("isolation_provider_unverified");
  });

  it("rejects a proof pack bound to another conversation before digesting it", async () => {
    const pack = await sealedPack();

    expect(parseConversationProofPack(pack, { taskId: "run-2" })).toMatchObject(
      {
        available: false,
        code: "TASK_MISMATCH",
      },
    );
    await expect(
      verifyConversationProofPack(pack, { taskId: "run-2" }),
    ).resolves.toMatchObject({
      available: false,
      code: "TASK_MISMATCH",
    });
  });

  it("reports both seal and artifact tampering", async () => {
    const pack = await sealedPack();
    const tampered = {
      ...pack,
      answer: "changed",
      artifacts: [
        {
          ...pack.artifacts[0],
          storage: { ...pack.artifacts[0].storage, content: "d29ybGQ=" },
        },
      ],
    };

    await expect(verifyConversationProofPack(tampered)).resolves.toMatchObject({
      available: true,
      ok: false,
    });
    const result = await verifyConversationProofPack(tampered);
    expect(result.violations.map(({ code }) => code)).toEqual(
      expect.arrayContaining(["integrity_mismatch", "artifact_hash_mismatch"]),
    );
  });

  it("keeps transport request ids outside the sealed body and rejects arbitrary extensions", async () => {
    const pack = await sealedPack();
    const withRequestId = {
      ...pack,
      request_id: "req_0123456789abcdef0123456789abcdef",
    };
    await expect(
      verifyConversationProofPack(withRequestId),
    ).resolves.toMatchObject({ available: true, ok: true });

    const withExtension = await sealedPack({ unsupported: true });
    const result = await verifyConversationProofPack(withExtension);
    expect(result).toMatchObject({ available: true, ok: false });
    expect(result.violations).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          code: "unknown_top_level_field",
          ref: "unsupported",
        }),
      ]),
    );
  });

  it("fails closed on malformed artifact storage", async () => {
    const pack = await sealedPack({
      artifacts: [
        {
          name: "answer.txt",
          sha256: artifactSha256,
          bytes: artifactBytes.byteLength,
          storage: { embedded: true, encoding: "hex", content: "68656c6c6f" },
        },
      ],
    });

    expect(parseConversationProofPack(pack)).toMatchObject({
      available: false,
      code: "MALFORMED",
    });
  });
});
