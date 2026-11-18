import { describe, it, expect } from "vitest";
import { envVarGroups, envVarsEmpty } from "../envVarGroups";

describe("envVarGroups", () => {
  it("splits settings.agent into the three §11 groups", () => {
    const groups = envVarGroups({
      config: { agent_name: "my-agent", livekit_url: "wss://x" },
      secret_refs: ["A", "B", "GOOGLE_APPLICATION_CREDENTIALS_JSON"],
      secrets: ["A", "B"],
      credential_files: [{ environment_name: "GOOGLE_APPLICATION_CREDENTIALS_JSON" }],
    });
    expect(groups.secrets).toEqual(["A", "B"]);
    expect(groups.config).toEqual({ agent_name: "my-agent", livekit_url: "wss://x" });
    expect(groups.credentialFiles).toEqual([
      { environment_name: "GOOGLE_APPLICATION_CREDENTIALS_JSON" },
    ]);
  });

  it("reads the split fields, not secret_refs (which would double-list files)", () => {
    const groups = envVarGroups({
      secret_refs: ["A", "GOOGLE_APPLICATION_CREDENTIALS_JSON"],
      secrets: ["A"],
      credential_files: [{ environment_name: "GOOGLE_APPLICATION_CREDENTIALS_JSON" }],
    });
    // The credential file name must NOT appear in secrets.
    expect(groups.secrets).toEqual(["A"]);
  });

  it("is defensive against a missing or partial agent", () => {
    expect(envVarGroups(undefined)).toEqual({ secrets: [], config: {}, credentialFiles: [] });
    expect(envVarGroups({})).toEqual({ secrets: [], config: {}, credentialFiles: [] });
    expect(envVarGroups({ credential_files: [null, { environment_name: "" }] }).credentialFiles).toEqual([]);
  });

  it("envVarsEmpty is true only when every group is empty", () => {
    expect(envVarsEmpty({ secrets: [], config: {}, credentialFiles: [] })).toBe(true);
    expect(envVarsEmpty({ secrets: ["A"], config: {}, credentialFiles: [] })).toBe(false);
    expect(envVarsEmpty({ secrets: [], config: { k: "v" }, credentialFiles: [] })).toBe(false);
    expect(envVarsEmpty({ secrets: [], config: {}, credentialFiles: [{ environment_name: "x" }] })).toBe(false);
  });
});
