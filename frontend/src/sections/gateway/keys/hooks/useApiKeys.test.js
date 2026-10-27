import { describe, expect, it } from "vitest";
import { normalizeApiKey } from "./useApiKeys";

describe("normalizeApiKey", () => {
  it("maps canonical snake_case API key fields to UI field names", () => {
    const normalized = normalizeApiKey({
      id: "key-id",
      gateway_key_id: "gateway-key-id",
      key_prefix: "pk-smoke",
      allowed_models: ["gpt-4o-mini"],
      allowed_providers: ["openai"],
      created_at: "2026-05-25T01:02:03Z",
      updated_at: "2026-05-25T02:03:04Z",
      last_used_at: "2026-05-25T03:04:05Z",
      expires_at: "2026-06-25T01:02:03Z",
      status: "active",
    });

    expect(normalized).toMatchObject({
      id: "key-id",
      gatewayKeyId: "gateway-key-id",
      keyPrefix: "pk-smoke",
      allowedModels: ["gpt-4o-mini"],
      allowedProviders: ["openai"],
      createdAt: "2026-05-25T01:02:03Z",
      updatedAt: "2026-05-25T02:03:04Z",
      lastUsedAt: "2026-05-25T03:04:05Z",
      expiresAt: "2026-06-25T01:02:03Z",
      status: "active",
    });
    expect(normalized.gateway_key_id).toBe("gateway-key-id");
  });

  it("preserves existing camelCase values when both shapes are present", () => {
    const normalized = normalizeApiKey({
      gatewayKeyId: "ui-gateway-key-id",
      gateway_key_id: "api-gateway-key-id",
      keyPrefix: "ui-prefix",
      key_prefix: "api-prefix",
      allowedModels: ["ui-model"],
      allowed_models: ["api-model"],
      allowedProviders: ["ui-provider"],
      allowed_providers: ["api-provider"],
    });

    expect(normalized).toMatchObject({
      gatewayKeyId: "ui-gateway-key-id",
      keyPrefix: "ui-prefix",
      allowedModels: ["ui-model"],
      allowedProviders: ["ui-provider"],
    });
  });
});
