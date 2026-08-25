import { describe, expect, it } from "vitest";

import {
  credentialCount,
  credentialValue,
  mergePastedCredentials,
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

  it("counts the synchronized union once", () => {
    expect(
      credentialCount(
        { SHARED: "environment", SECRET: "value" },
        { SHARED: "configuration", REGION: "value" },
      ),
    ).toBe(3);
  });
});
