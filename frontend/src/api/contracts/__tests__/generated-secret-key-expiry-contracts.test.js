import { describe, expect, it } from "vitest";

import {
  AccountsKeyGenerateSecretKeyBody,
  AccountsKeyGenerateSecretKeyResponse,
  AccountsKeyGetSecretKeysResponse,
} from "src/generated/api-contracts/api.zod";

// A key without expiry is sent and returned with expires_at: null.
const createdKey = (expiresAt) => ({
  status: true,
  result: {
    key_id: "4b0a7f0e-6d55-4f3c-9a51-2d2a8f0c1e11",
    key_name: "Synthetic key",
    api_key: "synthetic-api-key",
    masked_api_key: "synth****",
    secret_key: "synthetic-secret-key",
    masked_secret_key: "synth****",
    expires_at: expiresAt,
  },
});

const listedKeys = (expiresAt) => ({
  status: true,
  result: {
    metadata: { total_rows: 1, total_pages: 1, page_number: 1, page_size: 10 },
    table: [
      {
        id: "4b0a7f0e-6d55-4f3c-9a51-2d2a8f0c1e11",
        key_name: "Synthetic key",
        api_key: "synthetic-api-key",
        secret_key: "synthetic-secret-key",
        created_by: "Synthetic User",
        created_at: "2026-01-01T00:00:00Z",
        enabled: true,
        type: "user",
        expires_at: expiresAt,
        is_expired: false,
      },
    ],
  },
});

describe("generated API key expiry contracts", () => {
  it.each([[null], ["2027-01-01T00:00:00Z"]])(
    "accepts create body expires_at %s",
    (expiresAt) => {
      const body = { key_name: "Synthetic key", expires_at: expiresAt };
      expect(AccountsKeyGenerateSecretKeyBody.parse(body).expires_at).toBe(
        expiresAt,
      );
    },
  );

  it("accepts a create body without expires_at", () => {
    expect(
      AccountsKeyGenerateSecretKeyBody.parse({ key_name: "Synthetic key" })
        .expires_at,
    ).toBeUndefined();
  });

  it.each([[null], ["2027-01-01T00:00:00Z"]])(
    "accepts created key expires_at %s",
    (expiresAt) => {
      expect(
        AccountsKeyGenerateSecretKeyResponse.parse(createdKey(expiresAt)).result
          .expires_at,
      ).toBe(expiresAt);
    },
  );

  it.each([[null], ["2027-01-01T00:00:00Z"]])(
    "accepts listed key expires_at %s",
    (expiresAt) => {
      expect(
        AccountsKeyGetSecretKeysResponse.parse(listedKeys(expiresAt)).result
          .table[0].expires_at,
      ).toBe(expiresAt);
    },
  );

  it("still requires expires_at in the responses", () => {
    const created = createdKey(null);
    delete created.result.expires_at;
    expect(
      AccountsKeyGenerateSecretKeyResponse.safeParse(created).success,
    ).toBe(false);
    const listed = listedKeys(null);
    delete listed.result.table[0].expires_at;
    expect(AccountsKeyGetSecretKeysResponse.safeParse(listed).success).toBe(
      false,
    );
  });

  it("rejects an expires_at that is not a date-time", () => {
    expect(
      AccountsKeyGenerateSecretKeyBody.safeParse({
        key_name: "Synthetic key",
        expires_at: "tomorrow",
      }).success,
    ).toBe(false);
  });
});
