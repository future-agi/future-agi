import { describe, expect, it, vi } from "vitest";
import { RESERVED_KEY_METADATA, replacementPayload, rotateApiKey } from "../rotate";

const OLD = {
  id: "key-old", name: "support-bot", owner: "fixture-owner", project: "proj-1",
  status: "active", allowedModels: ["fixture-model"], allowedProviders: ["fixture-provider"],
  expiresAt: "2027-01-01T00:00:00Z",
  metadata: { env: "fixture", org_id: "org-1", rate_limit_rpm: 60,
    allowed_ips: "10.0.0.0/8,192.168.1.4", log_content: "metadata_only" },
};
const CREATED = {
  id: "new", key: "synthetic-only", status: "active",
  rotation: { status: "revoked", expiry_copied: true, gateway_revocation_confirmed: false },
};

const api = () => ({
  replaceAtomically: vi.fn().mockResolvedValue(CREATED),
  create: vi.fn(), update: vi.fn(), revoke: vi.fn(),
});

function noLegacy(calls) {
  expect(calls.create).not.toHaveBeenCalled();
  expect(calls.update).not.toHaveBeenCalled();
  expect(calls.revoke).not.toHaveBeenCalled();
}

describe("replacementPayload", () => {
  it("copies the key's name, owner, project and limits for server validation", () => {
    expect(replacementPayload(OLD)).toEqual({
      name: "support-bot", owner: "fixture-owner", project_id: "proj-1",
      allowed_models: ["fixture-model"], allowed_providers: ["fixture-provider"],
      rate_limit_rpm: 60, allowed_ips: ["10.0.0.0/8", "192.168.1.4"],
      log_content: "metadata_only", metadata: { env: "fixture" },
    });
  });
  it("leaves out settings the old key did not have", () => {
    expect(replacementPayload({ name: "n", metadata: { org_id: "o" } })).toEqual({
      name: "n", owner: "", allowed_models: [], allowed_providers: [], metadata: {},
    });
  });
});

describe("rotateApiKey", () => {
  it("creates, copies expiry and revokes through exactly one explicit command", async () => {
    const calls = api();
    const result = await rotateApiKey(OLD, calls);
    expect(calls.replaceAtomically).toHaveBeenCalledExactlyOnceWith(replacementPayload(OLD));
    expect(result).toEqual({
      secret: CREATED.key, created: CREATED, expiryCopied: true, oldRevoked: true,
      gatewayRevoked: false, revokeWarning: null,
    });
    noLegacy(calls);
  });
  it("does not issue a follow-up when replacement creation is rejected", async () => {
    const calls = api();
    calls.replaceAtomically.mockRejectedValueOnce(new Error("fixture error"));
    await expect(rotateApiKey(OLD, calls)).rejects.toThrow("fixture error");
    noLegacy(calls);
  });
  it("retains an active replacement with pending old-key revocation", async () => {
    const calls = api();
    calls.replaceAtomically.mockResolvedValueOnce({
      ...CREATED, rotation: { ...CREATED.rotation, warning: "fixture pending" },
    });
    const result = await rotateApiKey(OLD, calls);
    expect(result.secret).toBe(CREATED.key);
    expect(result.oldRevoked).toBe(true);
    expect(result.gatewayRevoked).toBe(false);
    expect(result.revokeWarning).toBe("fixture pending");
    noLegacy(calls);
  });
  it.each(["pending", "failed", undefined])("never reveals a %s replacement", async (status) => {
    const calls = api();
    calls.replaceAtomically.mockResolvedValueOnce({ ...CREATED, status });
    await expect(rotateApiKey(OLD, calls)).rejects.toThrow("Key rotation is not permitted");
    noLegacy(calls);
  });
  it("does not infer expiry or local revocation from incomplete acknowledgment", async () => {
    const calls = api();
    calls.replaceAtomically.mockResolvedValueOnce({ ...CREATED, rotation: {} });
    const result = await rotateApiKey(OLD, calls);
    expect(result.expiryCopied).toBe(false);
    expect(result.oldRevoked).toBe(false);
    expect(result.gatewayRevoked).toBe(false);
    noLegacy(calls);
  });
  it("has no legacy fallback when the command adapter is absent", async () => {
    const calls = api();
    delete calls.replaceAtomically;
    await expect(rotateApiKey(OLD, calls)).rejects.toThrow("Key rotation is not permitted");
    noLegacy(calls);
  });
  it("selects activated failure copy instead of rendering the provisioning code", async () => {
    const calls = api();
    calls.replaceAtomically.mockRejectedValueOnce({
      response: { data: { code: "KEY_PROVISIONING_FAILED" } },
    });
    const error = await rotateApiKey(OLD, calls).catch((value) => value);
    expect(error.message).toBe("");
    noLegacy(calls);
  });
  it.each([
    [{ status: "revoked", gateway_revocation_confirmed: true }, true],
    [{ status: "revoked", gateway_revocation_confirmed: true, warning: "pending" }, false],
    [{ status: "revoked" }, false],
    [{ status: "active", gateway_revocation_confirmed: true }, false],
  ])("only projects explicit non-contradictory backend confirmation", async (rotation, expected) => {
    const calls = api();
    calls.replaceAtomically.mockResolvedValueOnce({ ...CREATED, rotation });
    expect((await rotateApiKey(OLD, calls)).gatewayRevoked).toBe(expected);
    noLegacy(calls);
  });
});

describe("rotating a key with reserved metadata", () => {
  it("knows the gateway's reserved names, org_id aside", () => {
    expect([...RESERVED_KEY_METADATA].sort()).toEqual([
      "access_groups", "allow_policy_override", "id", "key_type", "mcp_personal",
      "name", "owner", "role", "team", "type",
    ].sort());
  });
  it("refuses before issuance rather than widening authority", async () => {
    const calls = api();
    await expect(rotateApiKey({ ...OLD, metadata: { team: "fixture", role: "x" } }, calls))
      .rejects.toThrow("its metadata sets role and team, which the console cannot copy to a new key. Rotate it where it was created");
    expect(calls.replaceAtomically).not.toHaveBeenCalled();
    noLegacy(calls);
  });
});
