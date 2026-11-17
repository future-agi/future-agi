import { describe, expect, it } from "vitest";
import * as policy from "./admissionPolicy";

// Assertion guards keep the r1 base RED without an import/collection failure.
function fn(name) {
  expect(policy[name], `${name} must be exported`).toBeTypeOf("function");
  return policy[name];
}

const SPELLINGS = ["max_run_usd", "maxRunUsd", "maxRunUSD"];
const BINDING_SPELLINGS = ["key_bindings", "keyBindings"];

function stored(agents, budgets = {}) {
  return {
    organization: "org-a",
    budgets: {
      enabled: true,
      admission_caps_enabled: true,
      policy_revision: 7,
      fail_mode: "closed",
      no_max_tokens: "clamp",
      agents: { worker: { limit: 5, period: "daily" } },
      key_ids: {},
      ...budgets,
    },
    routing: { strategy: "cost", agents },
  };
}

const ALIAS_FIXTURES = {
  maxRunUSD: { agents: { maxRunUSD: 0.1 }, effective: 0.1 },
  maxRunUsd: { agents: { maxRunUsd: 0.2 }, effective: 0.2 },
  "maxRunUsd beats maxRunUSD": {
    agents: { maxRunUsd: 0.2, maxRunUSD: 0.1 },
    effective: 0.2,
  },
  "canonical beats both aliases": {
    agents: { max_run_usd: 0.3, maxRunUsd: 0.2, maxRunUSD: 0.1 },
    effective: 0.3,
  },
  "a present canonical null shadows aliases": {
    agents: { max_run_usd: null, maxRunUSD: 0.1 },
    effective: null,
  },
};

describe("backend alias normalization (AliasChoices precedence)", () => {
  it("canonicalizes run-ceiling spellings with the contract precedence", () => {
    const normalize = fn("normalizeAgents");
    expect(normalize({ maxRunUSD: 0.1 })).toEqual({ max_run_usd: 0.1 });
    expect(normalize({ maxRunUsd: 0.2, maxRunUSD: 0.1 })).toEqual({
      max_run_usd: 0.2,
    });
    expect(normalize({ max_run_usd: 0.3, maxRunUSD: 0.1 })).toEqual({
      max_run_usd: 0.3,
    });
    // Presence, not truthiness, decides: a canonical null still wins.
    expect(normalize({ max_run_usd: null, maxRunUSD: 0.1 })).toEqual({
      max_run_usd: null,
    });
  });
  it("canonicalizes key bindings and the unpriced policy the same way", () => {
    const normalize = fn("normalizeAgents");
    expect(normalize({ keyBindings: { k: "a" } })).toEqual({
      key_bindings: { k: "a" },
    });
    expect(
      normalize({ keyBindings: { k: "a" }, key_bindings: { j: "b" } }),
    ).toEqual({ key_bindings: { j: "b" } });
    expect(normalize({ unpricedUnderCap: "reject" })).toEqual({
      unpriced_under_cap: "reject",
    });
  });
  it("leaves unrelated fields alone, never mutates and tolerates non-objects", () => {
    const normalize = fn("normalizeAgents");
    const input = { maxRunUSD: 1, defaultModel: "m", envelopes: { a: 1 } };
    const copy = JSON.parse(JSON.stringify(input));
    expect(normalize(input)).toEqual({
      max_run_usd: 1,
      defaultModel: "m",
      envelopes: { a: 1 },
    });
    expect(input).toEqual(copy);
    for (const value of [null, undefined, 3, "x", []])
      expect(normalize(value)).toEqual({});
  });
  it("canonicalizes the budget fields the panel reads", () => {
    const normalize = fn("normalizeBudgets");
    expect(
      normalize({
        policyRevision: 7,
        keyIds: { stable: { limit: 2 } },
        admissionCapsEnabled: true,
        failMode: "closed",
        noMaxTokens: "clamp",
        enabled: true,
      }),
    ).toEqual({
      policy_revision: 7,
      key_ids: { stable: { limit: 2 } },
      admission_caps_enabled: true,
      fail_mode: "closed",
      no_max_tokens: "clamp",
      enabled: true,
    });
  });
});

describe("display, draft initialization and configured state", () => {
  it.each(Object.entries(ALIAS_FIXTURES))(
    "reads %s into the policy view, the draft and the configured state",
    (_name, { agents, effective }) => {
      const config = stored(agents);
      expect(fn("readPolicy")(config).runCeiling).toBe(effective);
      expect(fn("admissionDraft")(config).runLimit).toBe(
        effective === null ? "" : String(effective),
      );
    },
  );
  it("detects a run-only alias policy as configured so it can be deactivated", () => {
    const readPolicy = fn("readPolicy");
    const runOnly = {
      organization: "org-a",
      budgets: { policy_revision: 4 },
      routing: { agents: { maxRunUSD: 0.1 } },
    };
    expect(readPolicy(runOnly)).toMatchObject({
      enabled: false,
      runCeiling: 0.1,
      configured: true,
    });
    expect(
      readPolicy({ organization: "org-a", budgets: {}, routing: {} }),
    ).toMatchObject({ configured: false, runCeiling: null });
    expect(readPolicy(null)).toMatchObject({ configured: false });
  });
  it("detects an alias-spelled enabled flag and key caps", () => {
    const view = fn("readPolicy")({
      organization: "org-a",
      budgets: {
        admissionCapsEnabled: true,
        keyIds: { stable: { limit: 2, activatedAt: "2026-10-27T12:00:00Z" } },
      },
      routing: {},
    });
    expect(view.enabled).toBe(true);
    expect(view.keyCaps).toEqual([
      expect.objectContaining({
        id: "stable",
        limit: 2,
        activated_at: "2026-10-27T12:00:00Z",
      }),
    ]);
  });
  it("initializes binding rows from either stored spelling", () => {
    const draft = fn("admissionDraft");
    for (const spelling of BINDING_SPELLINGS) {
      expect(
        draft(stored({ [spelling]: { stable: "worker", other: "worker" } }))
          .bindings,
      ).toEqual([
        { key: "stable", agent: "worker" },
        { key: "other", agent: "worker" },
      ]);
    }
    expect(draft(null).bindings).toEqual([]);
  });
  it("reads the CAS revision from either spelling and rejects an unsafe one", () => {
    const revision = fn("policyRevision");
    expect(revision({ budgets: { policyRevision: 9 } })).toBe(9);
    expect(
      revision({ budgets: { policy_revision: 4, policyRevision: 9 } }),
    ).toBe(4);
    expect(revision(null)).toBe(0);
    expect(() => revision({ budgets: { policyRevision: -1 } })).toThrow(
      /revision/i,
    );
  });
});

describe("clearing removes every stored spelling (with CAS)", () => {
  const blankRun = (config) => ({
    ...fn("admissionDraft")(config),
    runLimit: "",
  });
  it.each(Object.entries(ALIAS_FIXTURES))(
    "blank-run activation drops %s",
    (_name, { agents }) => {
      const config = stored({ ...agents, keyBindings: { stable: "worker" } });
      const payload = fn("admissionPayload")(config, blankRun(config), true);
      for (const spelling of SPELLINGS)
        expect(payload.routing.agents).not.toHaveProperty(spelling);
      expect(payload.routing.agents.key_bindings).toEqual({
        stable: "worker",
      });
      expect(payload.routing.agents).not.toHaveProperty("keyBindings");
      expect(payload.budgets.policy_revision).toBe(7);
    },
  );
  it.each(Object.entries(ALIAS_FIXTURES))(
    "deactivation drops %s and keeps bindings",
    (_name, { agents }) => {
      const config = stored({ ...agents, keyBindings: { stable: "worker" } });
      const payload = fn("admissionPayload")(config, null, false);
      for (const spelling of SPELLINGS)
        expect(payload.routing.agents).not.toHaveProperty(spelling);
      expect(payload.routing.agents).toEqual({
        key_bindings: { stable: "worker" },
      });
      expect(payload.budgets).toMatchObject({
        policy_revision: 7,
        admission_caps_enabled: false,
        enabled: true,
      });
    },
  );
  it("replaces every stored spelling with the single canonical new value", () => {
    const config = stored({
      maxRunUsd: 0.2,
      maxRunUSD: 0.1,
      keyBindings: { stable: "worker" },
    });
    const draft = { ...fn("admissionDraft")(config), runLimit: "0.5" };
    const payload = fn("admissionPayload")(config, draft, true);
    expect(payload.routing.agents.max_run_usd).toBe(0.5);
    expect(payload.routing.agents).not.toHaveProperty("maxRunUsd");
    expect(payload.routing.agents).not.toHaveProperty("maxRunUSD");
  });
  it("does not resurrect an alias-spelled unpriced policy or camelCase budgets", () => {
    const config = stored({
      unpricedUnderCap: "reject",
      keyBindings: { stable: "worker" },
    });
    config.budgets = {
      enabled: true,
      failMode: "closed",
      noMaxTokens: "clamp",
      admissionCapsEnabled: true,
      policyRevision: 7,
      agents: { worker: { limit: 5, period: "daily" } },
      keyIds: { stable: { limit: 2, period: "daily" } },
    };
    const payload = fn("admissionPayload")(config, null, false);
    expect(payload.budgets.policy_revision).toBe(7);
    expect(payload.budgets.key_ids).toEqual({
      stable: { limit: 2, period: "daily" },
    });
    for (const alias of [
      "policyRevision",
      "keyIds",
      "admissionCapsEnabled",
      "failMode",
      "noMaxTokens",
    ])
      expect(payload.budgets).not.toHaveProperty(alias);
    expect(payload.budgets.admission_caps_enabled).toBe(false);
    expect(payload.budgets.fail_mode).toBe("closed");
    expect(payload.routing.agents).not.toHaveProperty("unpricedUnderCap");
    expect(payload.routing.agents.unpriced_under_cap).toBe("reject");
  });
});
