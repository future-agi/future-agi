import { describe, expect, it } from "vitest";

import { AgentccApiKeysBulkListResponse } from "src/generated/api-contracts/api.zod";

// One key as gateway_key_payload emits it for GET /agentcc/api-keys/bulk/.
const key = {
  id: "gw-perpetual",
  name: "Production key",
  owner: "",
  key_hash: "b".repeat(64),
  key_prefix: "sk-agentcc-b...",
  models: [],
  providers: [],
  metadata: { org_id: "8f2c9e61-2b1c-4a5e-9d0f-6a4b7c1d2e3f" },
  expires_at: null,
};

describe("generated AgentCC bulk key contract", () => {
  it.each([
    ["a never-expiring key", null],
    ["an expiring key", "2026-10-06T12:00:00+00:00"],
  ])("accepts %s", (_name, expiresAt) => {
    const payload = {
      status: true,
      result: [{ ...key, expires_at: expiresAt }],
    };

    expect(AgentccApiKeysBulkListResponse.parse(payload)).toEqual(payload);
  });

  it("still requires expires_at on every key", () => {
    const { expires_at: _omitted, ...withoutExpiry } = key;
    const parsed = AgentccApiKeysBulkListResponse.safeParse({
      status: true,
      result: [withoutExpiry],
    });

    expect(parsed.success).toBe(false);
    expect(parsed.error.issues[0].path).toEqual(["result", 0, "expires_at"]);
  });
});
