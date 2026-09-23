import { describe, it, expect, vi, beforeEach } from "vitest";

// Echo the requested aliases back as opaque refs so the merge is observable.
vi.mock("src/api/harness/harness", () => ({
  storeHarnessSecretValues: vi.fn(),
}));

const { storeHarnessSecretValues } = await import("src/api/harness/harness");
const { prepareSourceForBuild, redactSource } = await import(
  "../prepareSourceForBuild"
);

beforeEach(() => {
  storeHarnessSecretValues.mockReset();
  // Return one ref per requested alias, prefixed so it can't be mistaken for raw.
  storeHarnessSecretValues.mockImplementation(async (values) => ({
    secret_refs: Object.fromEntries(
      Object.keys(values).map((alias) => [alias, `ref://${alias}`]),
    ),
  }));
});

describe("redactSource", () => {
  it("strips plaintext apiKey and envText but keeps opaque fields", () => {
    const safe = redactSource({
      kind: "platform",
      apiKey: "sk-secret",
      envText: "A=1",
      secret_refs: { A: "ref://A" },
      secretFiles: [{ name: "f", secret_ref: "sref-1" }],
    });
    expect(safe.apiKey).toBeUndefined();
    expect(safe.envText).toBeUndefined();
    expect(safe.secret_refs).toEqual({ A: "ref://A" });
    expect(safe.secretFiles).toEqual([{ name: "f", secret_ref: "sref-1" }]);
  });

  it("tolerates a null/undefined source", () => {
    expect(redactSource(undefined)).toEqual({});
    expect(redactSource(null)).toEqual({});
  });
});

describe("prepareSourceForBuild", () => {
  it("exchanges a hosted provider's apiKey for its fixed alias and redacts the raw key", async () => {
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "platform",
      provider: "vapi",
      agentId: "asst_x",
      apiKey: "sk-live-123",
    });
    expect(storeHarnessSecretValues).toHaveBeenCalledWith({ VAPI_API_KEY: "sk-live-123" });
    expect(draft.secret_refs).toEqual({ VAPI_API_KEY: "ref://VAPI_API_KEY" });
    expect(draft.apiKey).toBeUndefined();
    // The raw value rides alongside for the live probe (credential_values).
    expect(credentialValues).toEqual({ VAPI_API_KEY: "sk-live-123" });
  });

  it("exchanges pasted .env assignments, one alias per line", async () => {
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "repo",
      value: "acme/bot",
      envText: "OPENAI_API_KEY=sk-1\nSTRIPE_KEY=rk-2",
    });
    expect(storeHarnessSecretValues).toHaveBeenCalledWith({
      OPENAI_API_KEY: "sk-1",
      STRIPE_KEY: "rk-2",
    });
    expect(draft.secret_refs).toEqual({
      OPENAI_API_KEY: "ref://OPENAI_API_KEY",
      STRIPE_KEY: "ref://STRIPE_KEY",
    });
    expect(draft.envText).toBeUndefined();
    expect(credentialValues).toEqual({ OPENAI_API_KEY: "sk-1", STRIPE_KEY: "rk-2" });
  });

  it("merges a provider key and .env values into one exchange", async () => {
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "platform",
      provider: "retell",
      agentId: "ag_1",
      apiKey: "key-r",
      envText: "EXTRA=e1",
    });
    expect(storeHarnessSecretValues).toHaveBeenCalledWith({
      RETELL_API_KEY: "key-r",
      EXTRA: "e1",
    });
    expect(Object.keys(draft.secret_refs)).toEqual(
      expect.arrayContaining(["RETELL_API_KEY", "EXTRA"]),
    );
    expect(credentialValues).toEqual({ RETELL_API_KEY: "key-r", EXTRA: "e1" });
  });

  it("does not exchange when there is nothing to exchange", async () => {
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "repo",
      value: "acme/bot",
      envText: null,
    });
    expect(storeHarnessSecretValues).not.toHaveBeenCalled();
    expect("secret_refs" in draft).toBe(false);
    expect(credentialValues).toEqual({});
  });

  it("does not exchange a provider that has no single-key alias (livekit)", async () => {
    const { draft, credentialValues } = await prepareSourceForBuild({
      kind: "platform",
      provider: "livekit",
      agentId: "lk_1",
      apiKey: "should-drop",
    });
    expect(storeHarnessSecretValues).not.toHaveBeenCalled();
    expect("secret_refs" in draft).toBe(false);
    // The unexchangeable key is still redacted, never persisted, and never probed.
    expect(draft.apiKey).toBeUndefined();
    expect(credentialValues).toEqual({});
  });
});
