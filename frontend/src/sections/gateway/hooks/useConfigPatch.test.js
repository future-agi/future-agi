import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import {
  buildConfigPatch,
  configWarningMessage,
  guardrailsForSave,
  guardrailsWithPlaintextSecrets,
  mergePatch,
  postConfigPatch,
  useConfigPatch,
} from "./useConfigPatch";

const { post, snackbar } = vi.hoisted(() => ({
  post: vi.fn(),
  snackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post },
  endpoints: {
    gateway: {
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: snackbar }));

// The guardrails section as agentcc/services/guardrail_sync.py stores it: a
// `checks` list merged from the org's guardrail policies.
const SYNCED_GUARDRAILS = {
  enabled: true,
  checks: [
    {
      name: "pii-detector",
      enabled: true,
      action: "block",
      threshold: 0.6,
      config: { entities: ["EMAIL_ADDRESS"] },
      _policy: "policy-pii",
      _policy_name: "PII",
      _mode: "sync",
      _scope: "global",
    },
    {
      name: "openai-moderation",
      enabled: true,
      action: "warn",
      threshold: 0.9,
      config: { api_key: "__encrypted__" },
      _policy: "policy-mod",
      _policy_name: "Moderation",
      _mode: "sync",
      _scope: "global",
    },
  ],
};
const SYNCED_PII_RULE_DISABLED = {
  name: "pii-detector",
  stage: "pre",
  mode: "sync",
  action: "block",
  threshold: 0.6,
  enabled: false,
  config: { entities: ["EMAIL_ADDRESS"] },
  _policy: "policy-pii",
};
const SYNCED_MODERATION_RULE = {
  name: "openai-moderation",
  stage: "pre",
  mode: "sync",
  action: "warn",
  threshold: 0.9,
  enabled: true,
  config: { api_key: "__encrypted__" },
  _policy: "policy-mod",
};

let client;
const wrapper = ({ children }) =>
  React.createElement(QueryClientProvider, { client }, children);

beforeEach(() => {
  post.mockReset();
  snackbar.mockReset();
  client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
});

describe("mergePatch", () => {
  it("returns undefined when nothing changed", () => {
    expect(
      mergePatch({ a: 1, b: { c: [1, 2] } }, { a: 1, b: { c: [1, 2] } }),
    ).toBeUndefined();
  });

  it("sends only the leaves that changed", () => {
    expect(
      mergePatch(
        { failover: { enabled: true, max_attempts: 3 }, strategy: "cost" },
        { failover: { enabled: true, max_attempts: 5 }, strategy: "cost" },
      ),
    ).toEqual({ failover: { max_attempts: 5 } });
  });

  it("deletes a removed key with null, because update-config keeps absent keys", () => {
    expect(
      mergePatch(
        { model_fallbacks: { "gpt-4o": ["a"], o1: ["b"] } },
        { model_fallbacks: { "gpt-4o": ["a"] } },
      ),
    ).toEqual({ model_fallbacks: { o1: null } });
  });

  it("treats a null or undefined value in the new config as absent", () => {
    expect(mergePatch({ a: 1, b: 2 }, { a: null, b: undefined })).toEqual({
      a: null,
      b: null,
    });
    expect(mergePatch({}, { a: null, b: undefined })).toBeUndefined();
  });

  it("replaces an array wholesale when it differs", () => {
    expect(
      mergePatch({ codes: [429, 500, 502] }, { codes: [429, 502] }),
    ).toEqual({ codes: [429, 502] });
  });

  it("sends the whole new value when the type changes", () => {
    expect(mergePatch({ a: "x" }, { a: { b: 1 } })).toEqual({ a: { b: 1 } });
    expect(mergePatch({ a: { b: 1 } }, { a: [1] })).toEqual({ a: [1] });
  });

  it("clears a nested object key by key", () => {
    expect(mergePatch({ retry: { enabled: true } }, { retry: {} })).toEqual({
      retry: { enabled: null },
    });
  });
});

describe("buildConfigPatch", () => {
  it("keeps only the sections that changed", () => {
    const before = {
      routing: { strategy: "cost" },
      cache: { enabled: false },
      managed_models: { enabled: true },
    };
    const after = {
      routing: { strategy: "latency" },
      cache: { enabled: false },
    };
    expect(buildConfigPatch(before, after, ["routing", "cache"])).toEqual({
      routing: { strategy: "latency" },
    });
  });

  it("returns an empty patch when no section changed", () => {
    expect(
      buildConfigPatch({ routing: { a: 1 } }, { routing: { a: 1 } }, [
        "routing",
      ]),
    ).toEqual({});
  });

  it("treats a missing section as empty", () => {
    expect(
      buildConfigPatch(undefined, { budgets: { daily: 10 } }, ["budgets"]),
    ).toEqual({ budgets: { daily: 10 } });
  });
});

describe("guardrailsForSave", () => {
  const SAVED = {
    fail_open: false,
    rules: [
      {
        name: "openai-moderation",
        enabled: true,
        config: { api_key: "__encrypted__" },
        _policy: "policy-1",
      },
    ],
  };

  it("returns the saved section for an untouched draft", () => {
    expect(guardrailsForSave(null, SAVED)).toBe(SAVED);
    expect(guardrailsForSave({ ...SAVED }, SAVED)).toBe(SAVED);
  });

  // A rule without an action or a threshold is pushed with block and 0.8
  // (agentcc/services/config_push.py), so a check that carries neither is
  // saved without them.
  it("turns an edited checks map back into rules, keeping names, provider and _policy, and adding no action or threshold", () => {
    const edited = {
      fail_open: true,
      rules: SAVED.rules,
      checks: {
        "openai-moderation": {
          enabled: false,
          action: "warn",
          confidence_threshold: 0.6,
          config: { api_key: "__encrypted__" },
          _originalName: "openai-moderation",
        },
        "pii-detection": { enabled: true },
        "lakera-guard": { enabled: true, provider: "lakera", config: {} },
      },
    };

    expect(guardrailsForSave(edited, SAVED)).toEqual({
      fail_open: true,
      rules: [
        {
          name: "openai-moderation",
          stage: "pre",
          mode: "sync",
          action: "warn",
          threshold: 0.6,
          enabled: false,
          config: { api_key: "__encrypted__" },
          _policy: "policy-1",
        },
        {
          name: "pii-detector",
          stage: "pre",
          mode: "sync",
          enabled: true,
          config: {},
        },
        {
          name: "lakera-guard",
          stage: "pre",
          mode: "sync",
          enabled: true,
          config: { provider: "lakera" },
        },
      ],
    });
  });

  it("leaves a draft without a checks map as it is", () => {
    const edited = { rules: [], fail_open: true };
    expect(guardrailsForSave(edited, SAVED)).toBe(edited);
  });

  it("empties a checks list the policy sync stored, keeping each check's policy and threshold", () => {
    const edited = {
      enabled: true,
      // What the rules editor makes of SYNCED_GUARDRAILS, one check disabled.
      checks: {
        "pii-detection": {
          ...SYNCED_GUARDRAILS.checks[0],
          _originalName: "pii-detector",
          enabled: false,
        },
        "openai-moderation": {
          ...SYNCED_GUARDRAILS.checks[1],
          _originalName: "openai-moderation",
        },
      },
    };

    expect(guardrailsForSave(edited, SYNCED_GUARDRAILS)).toEqual({
      enabled: true,
      checks: [],
      rules: [SYNCED_PII_RULE_DISABLED, SYNCED_MODERATION_RULE],
    });
  });

  it("keeps a stored checks map as a map, putting back a policy the draft lost", () => {
    const saved = {
      checks: {
        "openai-moderation": {
          enabled: true,
          action: "block",
          confidence_threshold: 0.7,
          config: { api_key: "__encrypted__" },
          _policy: "policy-1",
        },
      },
    };
    // The editor's draft lost `_policy`; the saved map still has it.
    const edited = {
      checks: {
        "openai-moderation": {
          enabled: false,
          action: "block",
          confidence_threshold: 0.7,
          config: { api_key: "__encrypted__" },
        },
      },
    };

    expect(guardrailsForSave(edited, saved)).toEqual({
      checks: {
        "openai-moderation": {
          enabled: false,
          action: "block",
          confidence_threshold: 0.7,
          config: { api_key: "__encrypted__" },
          _policy: "policy-1",
        },
      },
    });
  });

  // The config push forwards a stored checks map to the gateway as stored
  // (agentcc/services/config_push.py), and the gateway runs a check stored
  // without an action as log, and one without a threshold at 0: any score
  // above 0 triggers it. A rule cannot leave them out: the push gives it
  // block and 0.8. So the map is saved as a map.
  it("keeps what each check of a stored checks map does: an edit adds no action or threshold", () => {
    // A map the gateway contract takes: a check holds enabled, action,
    // confidence_threshold and config only, its provider in its config.
    const saved = {
      checks: {
        "dynamoai-guard": {
          enabled: true,
          config: {
            provider: "dynamoai",
            text_type: "MODEL_RESPONSE",
            api_key: "__encrypted__",
            policy_ids: "p1",
          },
          _policy: "policy-1",
        },
        "data-leakage-prevention": { enabled: true, action: "block" },
      },
    };
    // The rules editor's draft after the admin edits DynamoAI's Policy IDs.
    const edited = {
      checks: {
        ...saved.checks,
        "dynamoai-guard": {
          ...saved.checks["dynamoai-guard"],
          config: {
            ...saved.checks["dynamoai-guard"].config,
            policy_ids: "p1, p2",
          },
        },
      },
    };

    const toSave = guardrailsForSave(edited, saved);

    expect(toSave).toEqual(edited);
    expect(toSave.rules).toBeUndefined();
    // The edited check stored without an action and a threshold keeps none,
    // and so does the untouched check stored without a threshold.
    expect(toSave.checks["dynamoai-guard"]).not.toHaveProperty("action");
    expect(toSave.checks["dynamoai-guard"]).not.toHaveProperty(
      "confidence_threshold",
    );
    expect(toSave.checks["data-leakage-prevention"]).not.toHaveProperty(
      "confidence_threshold",
    );
    // update-config gets the one field the admin edited.
    expect(
      buildConfigPatch({ guardrails: saved }, { guardrails: toSave }, [
        "guardrails",
      ]),
    ).toEqual({
      guardrails: {
        checks: { "dynamoai-guard": { config: { policy_ids: "p1, p2" } } },
      },
    });
  });

  // The rules editor gives a check it switches on or creates a provider of
  // its own. The gateway reads an external check's provider from its config,
  // and the gateway contract takes no provider on the check itself, so
  // update-config refuses such a map (agentcc/contracts/gateway_admin.py,
  // GuardrailCheck). A rule gets its provider into its config the same way.
  it("moves a check-level provider into the check's config, where the gateway contract takes it", () => {
    const saved = {
      checks: {
        "data-leakage-prevention": { enabled: true, action: "block" },
        "dynamoai-guard": {
          enabled: false,
          config: { provider: "dynamoai", policy_ids: "p1" },
        },
      },
    };
    // The admin switches DynamoAI on, and switches Lakera on, new to the map.
    const edited = {
      checks: {
        ...saved.checks,
        "dynamoai-guard": {
          ...saved.checks["dynamoai-guard"],
          provider: "dynamoai",
          enabled: true,
        },
        "lakera-guard": {
          action: "block",
          confidence_threshold: 0.8,
          provider: "lakera",
          enabled: true,
        },
      },
    };

    const toSave = guardrailsForSave(edited, saved);

    expect(toSave).toEqual({
      checks: {
        "data-leakage-prevention": { enabled: true, action: "block" },
        "dynamoai-guard": {
          enabled: true,
          config: { provider: "dynamoai", policy_ids: "p1" },
        },
        "lakera-guard": {
          action: "block",
          confidence_threshold: 0.8,
          enabled: true,
          config: { provider: "lakera" },
        },
      },
    });
    expect(
      buildConfigPatch({ guardrails: saved }, { guardrails: toSave }, [
        "guardrails",
      ]),
    ).toEqual({
      guardrails: {
        checks: {
          "dynamoai-guard": { enabled: true },
          "lakera-guard": {
            action: "block",
            confidence_threshold: 0.8,
            enabled: true,
            config: { provider: "lakera" },
          },
        },
      },
    });
  });

  it("saves rules over an empty checks map, which the push reads as absent", () => {
    const saved = {
      checks: {},
      rules: [
        {
          name: "pii-detector",
          enabled: true,
          action: "warn",
          threshold: 0.5,
          config: {},
        },
      ],
    };
    // What the rules editor makes of `saved`, the check disabled.
    const edited = {
      rules: saved.rules,
      checks: {
        "pii-detection": {
          enabled: false,
          action: "warn",
          confidence_threshold: 0.5,
          config: {},
          _originalName: "pii-detector",
        },
      },
    };

    expect(guardrailsForSave(edited, saved)).toEqual({
      checks: {},
      rules: [
        {
          name: "pii-detector",
          stage: "pre",
          mode: "sync",
          action: "warn",
          threshold: 0.5,
          enabled: false,
          config: {},
        },
      ],
    });
  });
});

// The keys a body sets to null directly under a section.
const sectionRootNulls = (body) =>
  Object.entries(body).flatMap(([section, value]) =>
    Object.entries(value || {})
      .filter(([, entry]) => entry === null)
      .map(([key]) => `${section}.${key}`),
  );

describe("update-config bodies", () => {
  it("saves a synced checks list as rules without a null under guardrails", () => {
    const edited = {
      enabled: true,
      checks: {
        "pii-detection": {
          ...SYNCED_GUARDRAILS.checks[0],
          _originalName: "pii-detector",
          enabled: false,
        },
        "openai-moderation": {
          ...SYNCED_GUARDRAILS.checks[1],
          _originalName: "openai-moderation",
        },
      },
    };
    const body = buildConfigPatch(
      { guardrails: SYNCED_GUARDRAILS },
      { guardrails: guardrailsForSave(edited, SYNCED_GUARDRAILS) },
      ["guardrails"],
    );

    // The list is replaced by an empty one, so the push reads the rules.
    expect(body).toEqual({
      guardrails: {
        checks: [],
        rules: [SYNCED_PII_RULE_DISABLED, SYNCED_MODERATION_RULE],
      },
    });
    expect(sectionRootNulls(body)).toEqual([]);
  });

  it("saves an edit of a stored checks map as a patch of that map, below the section", () => {
    const saved = {
      checks: {
        "pii-detection": { enabled: true, _originalName: "pii-detector" },
        "keyword-blocklist": { enabled: true, config: { words: ["a"] } },
      },
    };
    const edited = {
      checks: {
        ...saved.checks,
        "pii-detection": { ...saved.checks["pii-detection"], enabled: false },
      },
    };
    const body = buildConfigPatch(
      { guardrails: saved },
      { guardrails: guardrailsForSave(edited, saved) },
      ["guardrails"],
    );

    // Only the field edited is sent, and the untouched check not at all.
    expect(body).toEqual({
      guardrails: { checks: { "pii-detection": { enabled: false } } },
    });
    expect(sectionRootNulls(body)).toEqual([]);
  });

  it("deletes a removed model mapping with a null directly under model_map", () => {
    // This is the body update-config takes to delete a mapping: model_map
    // is a flat map, so there is no deeper key to clear.
    const body = buildConfigPatch(
      { model_map: { fast: "gpt-4o-mini", smart: "gpt-4o" } },
      { model_map: { fast: "gpt-4o-mini" } },
      ["model_map"],
    );

    expect(body).toEqual({ model_map: { smart: null } });
    expect(sectionRootNulls(body)).toEqual(["model_map.smart"]);
  });
});

describe("guardrailsWithPlaintextSecrets", () => {
  it("names rules whose config carries a credential in clear text", () => {
    expect(
      guardrailsWithPlaintextSecrets({
        rules: [
          { name: "openai-moderation", config: { api_key: "sk-test-value" } },
          { name: "aws-comprehend", config: { secret_key: "__encrypted__" } },
          { name: "pii-detector", config: {} },
        ],
      }),
    ).toEqual(["openai-moderation"]);
  });

  it("checks the checks map, as a map or a list", () => {
    expect(
      guardrailsWithPlaintextSecrets({
        checks: { lakera: { config: { password: "hunter2" } } },
      }),
    ).toEqual(["lakera"]);
    expect(
      guardrailsWithPlaintextSecrets({
        checks: [{ name: "azure", config: { access_key: "abc" } }],
      }),
    ).toEqual(["azure"]);
  });

  it("does not count a credential the saved config already holds", () => {
    const saved = {
      rules: [{ name: "lakera", config: { api_key: "stored-value" } }],
    };
    expect(
      guardrailsWithPlaintextSecrets(
        {
          rules: [
            {
              name: "lakera",
              enabled: false,
              config: { api_key: "stored-value" },
            },
          ],
        },
        saved,
      ),
    ).toEqual([]);
    expect(
      guardrailsWithPlaintextSecrets(
        { rules: [{ name: "lakera", config: { api_key: "changed-value" } }] },
        saved,
      ),
    ).toEqual(["lakera"]);
  });

  it("accepts the encrypted placeholder, empty values and deletions", () => {
    expect(
      guardrailsWithPlaintextSecrets({
        rules: [{ name: "a", config: { api_key: "__encrypted__" } }],
        checks: { b: { config: { api_key: "" } }, c: null },
      }),
    ).toEqual([]);
    expect(guardrailsWithPlaintextSecrets(undefined)).toEqual([]);
  });
});

describe("configWarningMessage", () => {
  it("uses the server's message when there is one", () => {
    expect(configWarningMessage({ code: "x", message: "Read this." })).toBe(
      "Read this.",
    );
    expect(configWarningMessage("Plain warning")).toBe("Plain warning");
  });

  it("describes a managed-models change from its code", () => {
    expect(
      configWarningMessage({
        code: "managed_models_change",
        from: true,
        to: false,
      }),
    ).toMatch(/managed models are now off/i);
    expect(
      configWarningMessage({
        code: "managed_models_change",
        from: false,
        to: true,
      }),
    ).toMatch(/managed models are now on/i);
  });

  it("falls back to the code, then to a generic sentence", () => {
    expect(configWarningMessage({ code: "budget_removed" })).toBe(
      "budget_removed",
    );
    expect(configWarningMessage({})).toMatch(/warning/i);
  });
});

describe("postConfigPatch", () => {
  it("posts the patch to update-config and unwraps the result", async () => {
    post.mockResolvedValue({ data: { result: { version: 7 } } });

    const result = await postConfigPatch(
      "gw-1",
      { routing: { strategy: "cost" } },
      "Saving routing",
    );

    expect(result).toEqual({ version: 7 });
    expect(post).toHaveBeenCalledWith(
      "/agentcc/gateways/gw-1/update-config/",
      { routing: { strategy: "cost" } },
      expect.objectContaining({ timeout: 30000 }),
    );
  });

  it("names the action that timed out", async () => {
    post.mockRejectedValue(
      Object.assign(new Error("timeout of 30000ms exceeded"), {
        code: "ECONNABORTED",
      }),
    );

    await expect(
      postConfigPatch("gw-1", { cache: {} }, "Saving cache"),
    ).rejects.toThrow(/^Saving cache timed out/);
  });
});

describe("useConfigPatch", () => {
  it("posts the patch, then refreshes the org config, its history and the gateway config", async () => {
    post.mockResolvedValue({
      data: { result: { version: 9, gateway_synced: true } },
    });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useConfigPatch(), { wrapper });

    let saved;
    await act(async () => {
      saved = await result.current.mutateAsync({
        gatewayId: "gw-1",
        patch: { rate_limiting: { rpm: 60 } },
      });
    });

    expect(saved).toEqual({ version: 9, gateway_synced: true });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1]).toEqual({ rate_limiting: { rpm: 60 } });
    const keys = invalidate.mock.calls.map(([arg]) => arg.queryKey[0]);
    expect(keys).toEqual(
      expect.arrayContaining([
        "agentcc-org-config",
        "agentcc-org-config-history",
        "agentcc-gateway-config",
      ]),
    );
    expect(snackbar).toHaveBeenCalledWith(
      "Config saved",
      expect.objectContaining({ variant: "success" }),
    );
  });

  it("does not post an empty patch, which update-config rejects", async () => {
    const { result } = renderHook(() => useConfigPatch(), { wrapper });

    let saved;
    await act(async () => {
      saved = await result.current.mutateAsync({
        gatewayId: "gw-1",
        patch: {},
      });
    });

    expect(saved).toBeNull();
    expect(post).not.toHaveBeenCalled();
    expect(snackbar).toHaveBeenCalledWith(
      "No changes to save",
      expect.objectContaining({ variant: "info" }),
    );
  });

  it("reports a saved config the gateway did not pick up", async () => {
    post.mockResolvedValue({
      data: {
        result: {
          version: 10,
          gateway_synced: false,
          gateway_warning: "Config saved but gateway sync failed.",
        },
      },
    });
    const { result } = renderHook(() => useConfigPatch(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        gatewayId: "gw-1",
        patch: { cache: { enabled: true } },
      });
    });

    expect(snackbar).toHaveBeenCalledWith(
      "Config saved but gateway sync failed.",
      expect.objectContaining({ variant: "warning" }),
    );
  });

  it("reports warnings the save came back with", async () => {
    post.mockResolvedValue({
      data: {
        result: {
          version: 11,
          gateway_synced: true,
          warnings: [{ code: "managed_models_change", from: true, to: false }],
        },
      },
    });
    const { result } = renderHook(() => useConfigPatch(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        gatewayId: "gw-1",
        patch: { managed_models: { enabled: false } },
      });
    });

    expect(snackbar).toHaveBeenCalledWith(
      expect.stringMatching(/managed models are now off/i),
      expect.objectContaining({ variant: "warning" }),
    );
  });

  it("refuses a guardrails patch that carries a new credential value", async () => {
    const { result } = renderHook(() => useConfigPatch(), { wrapper });

    let caught;
    await act(async () => {
      caught = await result.current
        .mutateAsync({
          gatewayId: "gw-1",
          patch: {
            guardrails: {
              checks: {
                "openai-moderation": { config: { api_key: "sk-test-value" } },
              },
            },
          },
        })
        .catch((e) => e);
    });

    expect(caught).toBeInstanceOf(Error);
    expect(post).not.toHaveBeenCalled();
    expect(snackbar).toHaveBeenCalledWith(
      expect.stringMatching(/openai-moderation.*not saved from this page/),
      expect.objectContaining({ variant: "error" }),
    );
  });

  it("saves a guardrails patch whose credentials are already encrypted", async () => {
    post.mockResolvedValue({ data: { result: { version: 12 } } });
    const { result } = renderHook(() => useConfigPatch(), { wrapper });
    const patch = {
      guardrails: {
        rules: [
          {
            name: "openai-moderation",
            enabled: false,
            config: { api_key: "__encrypted__" },
            _policy: "policy-1",
          },
        ],
      },
    };

    await act(async () => {
      await result.current.mutateAsync({ gatewayId: "gw-1", patch });
    });

    expect(post).toHaveBeenCalledWith(
      "/agentcc/gateways/gw-1/update-config/",
      patch,
      expect.any(Object),
    );
  });

  it("refuses to save without a gateway rather than posting to a bad URL", async () => {
    const { result } = renderHook(() => useConfigPatch(), { wrapper });

    let caught;
    await act(async () => {
      caught = await result.current
        .mutateAsync({ gatewayId: null, patch: { cache: { enabled: true } } })
        .catch((e) => e);
    });

    expect(caught).toBeInstanceOf(Error);
    expect(post).not.toHaveBeenCalled();
    expect(snackbar).toHaveBeenCalledWith(
      expect.stringMatching(/no gateway/i),
      expect.objectContaining({ variant: "error" }),
    );
  });

  it("shows the server's validation message when the save fails", async () => {
    post.mockRejectedValue({
      statusCode: 400,
      result: "Unknown config fields: providers",
    });
    const { result } = renderHook(() => useConfigPatch(), { wrapper });

    await act(async () => {
      await result.current
        .mutateAsync({ gatewayId: "gw-1", patch: { routing: { a: 1 } } })
        .catch(() => {});
    });

    expect(snackbar).toHaveBeenCalledWith(
      "Unknown config fields: providers",
      expect.objectContaining({ variant: "error" }),
    );
  });
});
