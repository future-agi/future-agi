import { describe, it, expect } from "vitest";

const { prepareSourceForBuild, redactSource } = await import(
  "../prepareSourceForBuild"
);

describe("redactSource", () => {
  it("strips plaintext apiKey, apiSecret and envText but keeps opaque fields", () => {
    const safe = redactSource({
      kind: "platform",
      apiKey: "sk-secret",
      apiSecret: "lk-secret",
      envText: "A=1",
      secret_refs: { A: "ref://A" },
      secretFiles: [{ name: "f", secret_ref: "sref-1" }],
    });
    expect(safe.apiKey).toBeUndefined();
    expect(safe.apiSecret).toBeUndefined();
    expect(safe.envText).toBeUndefined();
    expect(safe.secret_refs).toEqual({ A: "ref://A" });
    expect(safe.secretFiles).toEqual([{ name: "f", secret_ref: "sref-1" }]);
  });

  it("keeps a connected agent's id", () => {
    expect(
      redactSource({ apiKey: "sk-secret", agentDefinitionId: "def-1" }),
    ).toEqual({ agentDefinitionId: "def-1" });
  });

  it("tolerates a null/undefined source", () => {
    expect(redactSource(undefined)).toEqual({});
    expect(redactSource(null)).toEqual({});
  });
});

describe("prepareSourceForBuild", () => {
  it("maps a hosted provider's apiKey to its fixed alias and redacts the raw key", async () => {
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "platform",
      provider: "vapi",
      agentId: "asst_x",
      apiKey: "sk-live-123",
    });
    expect("secret_refs" in draft).toBe(false);
    expect(draft.apiKey).toBeUndefined();
    expect(credentialValues).toEqual({ VAPI_API_KEY: "sk-live-123" });
  });

  it("takes pasted .env assignments, one alias per line, and drops blank ones", async () => {
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "repo",
      value: "acme/bot",
      envText: "OPENAI_API_KEY=sk-1\nSTRIPE_KEY=rk-2\nEMPTY=",
    });
    expect(draft.envText).toBeUndefined();
    expect(credentialValues).toEqual({ OPENAI_API_KEY: "sk-1", STRIPE_KEY: "rk-2" });
  });

  it("merges a provider key and .env values", async () => {
    const { credentialValues } = await prepareSourceForBuild({
      kind: "platform",
      provider: "retell",
      agentId: "ag_1",
      apiKey: "key-r",
      envText: "EXTRA=e1",
    });
    expect(credentialValues).toEqual({ RETELL_API_KEY: "key-r", EXTRA: "e1" });
  });

  it("keeps only the staged credential files as refs", async () => {
    const fileRef = { manager: "platform-vault", key: "harness-google-adc-1" };
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "repo",
      value: "acme/bot",
      envText: "OPENAI_API_KEY=sk-1",
      secretFiles: [
        { environment_name: "GOOGLE_APPLICATION_CREDENTIALS_JSON", secret_ref: fileRef },
      ],
    });
    expect(draft.secret_refs).toEqual({
      GOOGLE_APPLICATION_CREDENTIALS_JSON: fileRef,
    });
    expect(JSON.stringify(draft)).not.toContain("sk-1");
    expect(credentialValues).toEqual({ OPENAI_API_KEY: "sk-1" });
  });

  it("has nothing to send when there are no credentials", async () => {
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "repo",
      value: "acme/bot",
      envText: null,
    });
    expect("secret_refs" in draft).toBe(false);
    expect(credentialValues).toEqual({});
  });

  it("takes the livekit key and secret and redacts both", async () => {
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "platform",
      provider: "livekit",
      agentId: "returns-agent",
      apiKey: " lk-key ",
      apiSecret: " lk-secret ",
      livekitUrl: "wss://demo.livekit.cloud",
    });
    expect(draft.apiKey).toBeUndefined();
    expect(draft.apiSecret).toBeUndefined();
    expect(draft.livekitUrl).toBe("wss://demo.livekit.cloud");
    expect(credentialValues).toEqual({
      LIVEKIT_API_KEY: "lk-key",
      LIVEKIT_API_SECRET: "lk-secret",
    });
  });
});
