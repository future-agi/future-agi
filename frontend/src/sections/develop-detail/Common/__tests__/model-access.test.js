// One-key adapter (ONEKEY-ASSUMED-CONTRACT-r1). Common/modelAccess.js is the
// only DSX frontend module that knows where the managed-or-BYOK verdict lives:
// a model-list item's credential_mode ('managed' | 'byok' | 'unavailable') and
// payer ('future_agi_credits' | 'own_provider_key' | null). These fixtures are
// contract-shaped server answers; no provider credential is involved.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SS_KEY_ORG_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

const net = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => net.get(...args) },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));

import {
  CREDENTIAL_MODE,
  EMBEDDING_ACCESS_MODELS,
  PAYER,
  embeddingAccessQueryKey,
  fetchEmbeddingAccess,
  fetchModelListEntry,
  readModelAccess,
} from "../modelAccess";

const MODEL_LIST = "/model-hub/api/models_list/";

const item = (over = {}) => ({
  model_name: "gpt-4o-mini",
  providers: "openai",
  is_available: true,
  logo_url: "",
  type: "chat",
  ...over,
});

describe("the pinned contract enums", () => {
  it("credential_mode and payer take exactly the contract values", () => {
    expect(Object.values(CREDENTIAL_MODE).sort()).toEqual([
      "byok",
      "managed",
      "unavailable",
    ]);
    expect(Object.values(PAYER).sort()).toEqual([
      "future_agi_credits",
      "own_provider_key",
    ]);
  });
});

describe("readModelAccess", () => {
  it("reads credential_mode, payer and is_available from a list item", () => {
    expect(
      readModelAccess(
        item({ credential_mode: "managed", payer: "future_agi_credits" }),
      ),
    ).toEqual({
      contract: true,
      mode: "managed",
      payer: "future_agi_credits",
      isAvailable: true,
      provider: "openai",
      modelName: "gpt-4o-mini",
    });
  });

  it("accepts the camelCase aliases a picked option carries", () => {
    expect(
      readModelAccess({
        modelName: "gpt-4o-mini",
        providers: "openai",
        isAvailable: true,
        credentialMode: "byok",
        payer: "own_provider_key",
      }),
    ).toMatchObject({
      contract: true,
      mode: "byok",
      payer: "own_provider_key",
      isAvailable: true,
    });
  });

  it("marks an item from a server without the one-key fields", () => {
    expect(readModelAccess(item({ is_available: false }))).toEqual({
      contract: false,
      mode: null,
      payer: null,
      isAvailable: false,
      provider: "openai",
      modelName: "gpt-4o-mini",
    });
  });

  it("never trusts an unrecognised credential_mode or payer", () => {
    expect(
      readModelAccess(item({ credential_mode: "trial", payer: "someone" })),
    ).toMatchObject({ contract: true, mode: null, payer: null });
    expect(readModelAccess(item({ credential_mode: null }))).toMatchObject({
      contract: true,
      mode: null,
    });
  });

  it("has no verdict without an item", () => {
    expect(readModelAccess(null)).toBeNull();
    expect(readModelAccess(undefined)).toBeNull();
    expect(readModelAccess("gpt-4o-mini")).toBeNull();
  });
});

describe("fetchModelListEntry", () => {
  // A returned function is a Vitest teardown, not a reset result. Returning
  // net.get would call the HTTP mock again with no arguments after each test.
  beforeEach(() => {
    net.get.mockReset();
  });

  it("asks models_list for that one model by name", async () => {
    net.get.mockResolvedValue({
      data: { results: [item({ credential_mode: "byok" })] },
    });

    await expect(fetchModelListEntry("GPT-4o-mini")).resolves.toMatchObject({
      model_name: "gpt-4o-mini",
      credential_mode: "byok",
    });
    expect(net.get).toHaveBeenCalledTimes(1);
    expect(net.get).toHaveBeenCalledWith(MODEL_LIST, {
      params: { name: "GPT-4o-mini" },
    });
  });

  it("is null when the server lists no such model", async () => {
    net.get.mockResolvedValue({ data: { results: [] } });
    await expect(fetchModelListEntry("gpt-4o-mini")).resolves.toBeNull();

    net.get.mockResolvedValue({
      data: { results: [item({ model_name: "gpt-4o" })] },
    });
    await expect(fetchModelListEntry("gpt-4o-mini")).resolves.toBeNull();
  });

  it("throws on a malformed answer so callers fail safe", async () => {
    net.get.mockResolvedValue({ data: {} });
    await expect(fetchModelListEntry("gpt-4o-mini")).rejects.toThrow();
  });
});

describe("fetchEmbeddingAccess", () => {
  // A returned function is a Vitest teardown, not a reset result. Returning
  // net.get would call the HTTP mock again with no arguments after each test.
  beforeEach(() => {
    net.get.mockReset();
  });

  it("looks up each embedding type's server default model", async () => {
    net.get.mockImplementation((_url, config) =>
      Promise.resolve({
        data: {
          results:
            config.params.name === "text-embedding-3-small"
              ? [
                  item({
                    model_name: "text-embedding-3-small",
                    type: "embedding",
                    credential_mode: "managed",
                    payer: "future_agi_credits",
                  }),
                ]
              : [],
        },
      }),
    );

    const access = await fetchEmbeddingAccess();

    expect(Object.keys(access).sort()).toEqual(["huggingface", "openai"]);
    expect(access.openai).toMatchObject({ credential_mode: "managed" });
    expect(access.huggingface).toBeNull();
    expect(net.get.mock.calls.map(([url]) => url)).toEqual([
      MODEL_LIST,
      MODEL_LIST,
    ]);
    expect(
      net.get.mock.calls.map(([, config]) => config.params.name).sort(),
    ).toEqual(Object.values(EMBEDDING_ACCESS_MODELS).sort());
  });

  it("a failed lookup gives no verdict, never a managed grant", async () => {
    net.get.mockRejectedValue(new Error("offline"));
    await expect(fetchEmbeddingAccess()).resolves.toEqual({
      openai: null,
      huggingface: null,
    });
  });
});

describe("embeddingAccessQueryKey", () => {
  afterEach(() => sessionStorage.clear());

  it("binds a cached verdict to the organization and workspace", () => {
    sessionStorage.setItem(SS_KEY_ORG_ID, "org-a");
    sessionStorage.setItem(SS_KEY_WORKSPACE_ID, "ws-1");
    expect(embeddingAccessQueryKey()).toEqual([
      "embedding-access",
      "org-a",
      "ws-1",
    ]);

    sessionStorage.setItem(SS_KEY_WORKSPACE_ID, "ws-2");
    expect(embeddingAccessQueryKey()).toEqual([
      "embedding-access",
      "org-a",
      "ws-2",
    ]);
  });
});


describe("compose canonical eligibility", () => {
  it("unknown present modes fail closed, absent modes preserve legacy", async () => {
    const { getModelEligibility } = await import("../modelAccess");
    expect(getModelEligibility(item({ credential_mode: "future-mode" }), true).selectable).toBe(false);
    expect(getModelEligibility(item(), true).selectable).toBe(true);
    expect(getModelEligibility(item({ credential_mode: "byok" }), true).selectable).toBe(true);
    expect(getModelEligibility(item({ credential_mode: "byok" }), false).selectable).toBe(false);
  });
  it("managed never sends a user to provider setup", async () => {
    const { getModelEligibility } = await import("../modelAccess");
    expect(getModelEligibility(item({ credential_mode: "managed", is_available: false }), false)).toMatchObject({selectable: false, needsProviderSetup: false, payerLabel: null});
  });
});
