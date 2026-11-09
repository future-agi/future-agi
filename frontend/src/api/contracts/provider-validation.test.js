import { describe, expect, it } from "vitest";

import {
  AgentccProviderCredentialsCreateBody,
  AgentccProviderCredentialsPartialUpdateBody,
} from "../../generated/api-contracts/api.zod";

// OQ3: defaults, AWS aliases, and numeric/provider-specific extra_config are
// valid payloads. Generated clients must not reject them before Django does.
describe("provider credential request contracts", () => {
  it("accepts omitted defaults and the empty operator-endpoint sentinel", () => {
    const minimal = {
      provider_name: "openai",
      credentials: { api_key: "synthetic-key" },
    };
    expect(
      AgentccProviderCredentialsCreateBody.safeParse(minimal).success,
    ).toBe(true);
    expect(
      AgentccProviderCredentialsCreateBody.safeParse({
        ...minimal,
        base_url: "",
        display_name: "",
      }).success,
    ).toBe(true);
  });

  it("keeps the OQ3 payload's aliases and arbitrary JSON extra_config", () => {
    const payload = {
      provider_name: "bedrock",
      credentials: {
        api_key: "synthetic-key",
        access_key: "synthetic-access",
        secret_key: "synthetic-secret",
        region: "us-east-1",
      },
      api_format: "bedrock",
      base_url: "https://provider.invalid/v1",
      extra_config: {
        api_path_prefix: "",
        weight: 3,
        nested: { enabled: true },
      },
    };
    expect(
      AgentccProviderCredentialsCreateBody.safeParse(payload).success,
    ).toBe(true);
    expect(
      AgentccProviderCredentialsPartialUpdateBody.safeParse({
        base_url: "",
        extra_config: payload.extra_config,
      }).success,
    ).toBe(true);
  });

  it("refuses a provider name used as an unsupported API format", () => {
    expect(
      AgentccProviderCredentialsCreateBody.safeParse({
        provider_name: "ollama",
        credentials: { api_key: "synthetic-key" },
        base_url: "https://provider.invalid",
        api_format: "ollama",
      }).success,
    ).toBe(false);
  });
});
