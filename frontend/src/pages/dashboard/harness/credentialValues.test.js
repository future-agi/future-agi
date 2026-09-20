import { describe, expect, it } from "vitest";

import {
  credentialCount,
  credentialValue,
  isSecretCredentialName,
  mergePastedCredentials,
  partitionConfigurationValues,
  updateCredential,
} from "./credentialValues";

describe("harness credential synchronization", () => {
  it("shows pasted values in preflight fields", () => {
    expect(
      credentialValue({ OPENAI_API_KEY: "pasted" }, {}, "OPENAI_API_KEY"),
    ).toBe("pasted");
  });

  it("merges pasted values without losing unrelated manual entries", () => {
    expect(
      mergePastedCredentials(
        { EXISTING_SECRET: "keep" },
        { REMOTE_AGENT_ID: "manual", REGION: "old" },
        { REGION: "pasted", NEW_SECRET: "new" },
      ),
    ).toEqual({
      environmentValues: {
        EXISTING_SECRET: "keep",
        REGION: "pasted",
        NEW_SECRET: "new",
      },
      configurationValues: { REMOTE_AGENT_ID: "manual" },
    });
  });

  it("edits a pasted value in place without creating a duplicate", () => {
    expect(
      updateCredential(
        { REGION: "pasted" },
        { REGION: "stale" },
        { name: "REGION", value: "edited", kind: "configuration" },
      ),
    ).toEqual({
      environmentValues: { REGION: "edited" },
      configurationValues: {},
    });
  });

  it.each([
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "ACCESS_TOKEN",
    "DATABASE_PASSWORD",
    "GOOGLE_PRIVATE_KEY",
  ])("recognizes %s as a secret even when source inspection does not", (name) => {
    expect(isSecretCredentialName(name)).toBe(true);
  });

  it("routes a misclassified discovered API key to ephemeral credentials", () => {
    expect(
      updateCredential(
        {},
        {},
        {
          name: "LIVEKIT_API_KEY",
          value: "entered-in-requirement-field",
          kind: "configuration",
        },
      ),
    ).toEqual({
      environmentValues: {
        LIVEKIT_API_KEY: "entered-in-requirement-field",
      },
      configurationValues: {},
    });
  });

  it("repairs stale mixed state again when constructing a request", () => {
    expect(
      partitionConfigurationValues({
        LIVEKIT_URL: "wss://example.livekit.cloud",
        LIVEKIT_API_KEY: "key",
        LIVEKIT_API_SECRET: "secret",
      }),
    ).toEqual({
      configurationValues: {
        LIVEKIT_URL: "wss://example.livekit.cloud",
      },
      environmentValues: {
        LIVEKIT_API_KEY: "key",
        LIVEKIT_API_SECRET: "secret",
      },
    });
  });

  it("counts the synchronized union once", () => {
    expect(
      credentialCount(
        { SHARED: "environment", SECRET: "value" },
        { SHARED: "configuration", REGION: "value" },
      ),
    ).toBe(3);
  });
});
