import { describe, expect, it } from "vitest";

import { CATALOG, catalogEntry, checkStage, needsSetup } from "../index";
import {
  ACTION_HELP,
  actionChoices,
  actionHelp,
  defaultActionFor,
  runsAsLog,
} from "../actions";
import {
  actionOf,
  formToConfig,
  formValuesFor,
  mapEditForSave,
  normalizeGuardrails,
  ruleBodyForSave,
  ruleLabel,
  ruleRows,
  sectionHasChecks,
  storedActionOf,
} from "../rule-model";

// The demo org's section, as GET org-configs/active/ returns it.
const DEMO = {
  rules: [
    {
      mode: "sync",
      name: "pii-detection",
      stage: "pre",
      action: "block",
      config: { entities: ["email", "ssn"] },
      enabled: true,
      threshold: 0.5,
    },
    {
      mode: "sync",
      name: "prompt-injection",
      stage: "pre",
      action: "warn",
      config: {},
      enabled: true,
      threshold: 0.6,
    },
    {
      name: "lakera-guard",
      action: "mask",
      enabled: false,
      threshold: 0.7,
      config: { provider: "lakera", api_key: "__encrypted__" },
      _policy: "policy-1",
    },
    {
      name: "acme-webhook",
      action: "block",
      enabled: true,
      config: { url: "https://hooks.example.test/check" },
    },
  ],
};

describe("guardrail catalog", () => {
  it("names a kind and the real stage for every check", () => {
    expect(CATALOG.length).toBe(28);
    CATALOG.forEach((entry) => {
      expect(["built-in", "partner", "futureagi"]).toContain(entry.kind);
      expect(entry.description).toBeTruthy();
    });
    // The gateway runs only these two on the model's answer
    // (agentcc-gateway internal/guardrails/{hallucination,leakage}).
    const responseSide = CATALOG.filter((e) => e.stage === "response").map(
      (e) => e.name,
    );
    expect(responseSide.sort()).toEqual([
      "data-leakage-prevention",
      "hallucination-detection",
    ]);
    expect(checkStage("lakera-guard")).toBe("request");
    expect(checkStage("some-custom-webhook")).toBe("request");
  });

  it("gives language detection and topic restriction the settings they need to do anything", () => {
    const language = catalogEntry("language-detection");
    expect(language.fields.map((f) => f.key)).toEqual(["allowed_scripts"]);
    expect(language.fields[0].required).toBe(true);
    const topic = catalogEntry("topic-restriction");
    expect(topic.fields.map((f) => f.key)).toEqual([
      "forbidden_topics",
      "allowed_topics",
    ]);
  });
});

describe("rule model", () => {
  it("reads mask as mask only where the check can rewrite the request, and unknown actions as log", () => {
    expect(actionOf({ action: "block" })).toBe("block");
    expect(actionOf({ action: "warn" })).toBe("warn");
    expect(actionOf({ action: "mask" }, { name: "pii-detection" })).toBe(
      "mask",
    );
    expect(actionOf({ action: "mask", name: "pii-detector" })).toBe("mask");
    expect(actionOf({ action: "mask" }, { name: "prompt-injection" })).toBe(
      "log",
    );
    expect(actionOf({})).toBe("block");
    expect(actionOf({ action: "nonsense" })).toBe("log");
  });

  // The config push gives a rule without an action or a threshold block and
  // 0.8; a checks map is forwarded as stored, and the gateway runs its check
  // without them as log, at 0.
  it("reads a check of a checks map stored without an action or threshold as log, at 0", () => {
    expect(actionOf({}, { asMap: true })).toBe("log");
    const rows = ruleRows(
      { checks: { "data-leakage-prevention": { enabled: true } } },
      { asMap: true },
    );
    expect(rows[0]).toMatchObject({ action: "log", threshold: 0 });
    expect(storedActionOf({ action: "mask" }, { asMap: true })).toBe("mask");
  });

  it("knows which checks cannot run until they are set up", () => {
    const blocklist = catalogEntry("keyword-blocklist");
    expect(needsSetup(blocklist, null)).toBe(true);
    expect(needsSetup(blocklist, { config: { words: [] } })).toBe(true);
    expect(needsSetup(blocklist, { config: { words: ["zeus"] } })).toBe(false);
    expect(needsSetup(catalogEntry("language-detection"), {})).toBe(true);
    expect(needsSetup(catalogEntry("topic-restriction"), {})).toBe(true);
    expect(
      needsSetup(catalogEntry("topic-restriction"), {
        config: {
          forbidden_topics: [{ name: "Legal", keywords: ["lawsuit"] }],
        },
      }),
    ).toBe(false);
    // A stored credential counts as set even though it is only a sentinel.
    expect(
      needsSetup(catalogEntry("lakera-guard"), {
        config: { api_key: "__encrypted__" },
      }),
    ).toBe(false);
    expect(needsSetup(catalogEntry("lakera-guard"), { config: {} })).toBe(true);
    expect(needsSetup(catalogEntry("pii-detection"), null)).toBe(false);
  });

  it("builds one row per configured rule, with its stats and custom rules kept", () => {
    const rows = ruleRows(normalizeGuardrails(DEMO), {
      ruleStats: [
        { rule: "pii-detection", trigger_count: 24, block_count: 24 },
        { rule: "prompt-injection", trigger_count: 1, warn_count: 1 },
      ],
      feedback: [{ check_name: "pii-detection", correct_rate: 75 }],
    });
    expect(rows.map((r) => r.id)).toEqual([
      "pii-detection",
      "prompt-injection",
      "lakera-guard",
      "acme-webhook",
    ]);
    const [pii, injection, lakera, custom] = rows;
    expect(pii).toMatchObject({
      label: "PII Detection",
      ruleName: "pii-detection",
      kind: "built-in",
      stage: "request",
      enabled: true,
      action: "block",
      // PII Detection scores 1.0 on a match: no threshold applies.
      threshold: null,
      triggers: 24,
      blocked: 24,
      warned: 0,
      accuracy: 75,
    });
    expect(injection).toMatchObject({ action: "warn", triggers: 1, warned: 1 });
    expect(injection.threshold).not.toBeNull();
    expect(lakera).toMatchObject({
      label: "Lakera Guard",
      kind: "partner",
      enabled: false,
      action: "log",
      accuracy: null,
    });
    expect(custom).toMatchObject({
      label: "acme-webhook",
      kind: "custom",
      stage: "request",
      triggers: 0,
    });
  });

  it("maps legacy rule names onto the catalog", () => {
    const rows = ruleRows(
      normalizeGuardrails({
        rules: [{ name: "pii-detector", action: "block", enabled: true }],
      }),
    );
    expect(rows[0]).toMatchObject({
      id: "pii-detection",
      ruleName: "pii-detector",
      label: "PII Detection",
    });
  });

  it("detects a section whose checks win over its rules in the config push", () => {
    expect(sectionHasChecks(DEMO)).toBe(false);
    expect(sectionHasChecks({ rules: [], checks: {} })).toBe(false);
    expect(sectionHasChecks({ checks: [{ name: "pii-detector" }] })).toBe(true);
    expect(sectionHasChecks({ checks: { "pii-detection": {} } })).toBe(true);
    expect(sectionHasChecks(null)).toBe(false);
  });
});

describe("saving a check's settings", () => {
  it("merges the form into the stored config, keeps blank credentials and drops cleared fields", () => {
    const meta = catalogEntry("lakera-guard");
    const config = formToConfig(
      meta,
      {
        api_key: "",
        endpoint: "https://api.lakera.ai/v2/guard",
        categories: [],
      },
      {
        provider: "lakera",
        api_key: "__encrypted__",
        categories: ["jailbreak"],
        custom_flag: true,
      },
    );
    expect(config).toEqual({
      provider: "lakera",
      api_key: "__encrypted__",
      endpoint: "https://api.lakera.ai/v2/guard",
      custom_flag: true,
    });
  });

  it("parses keyword lists and topic lists", () => {
    expect(
      formToConfig(
        catalogEntry("keyword-blocklist"),
        { words: "alpha\nbeta, gamma\nalpha" },
        { whole_word: false },
      ),
    ).toEqual({ words: ["alpha", "beta", "gamma"], whole_word: false });
    expect(
      formToConfig(
        catalogEntry("topic-restriction"),
        {
          forbidden_topics: "Legal: lawsuit, attorney\nweapons",
          allowed_topics: "",
        },
        {},
      ),
    ).toEqual({
      forbidden_topics: [
        { name: "Legal", keywords: ["lawsuit", "attorney"] },
        { name: "weapons", keywords: ["weapons"] },
      ],
    });
  });

  // No stage is added: the gateway never receives one, and runs an
  // organization's check where its type runs. A stage stored with a rule is
  // kept as stored (the next case).
  it("keeps the rule's policy and mode so an encrypted credential survives the save, and adds no stage", () => {
    const saved = normalizeGuardrails(DEMO);
    const body = ruleBodyForSave({
      saved: DEMO,
      check: saved.checks["lakera-guard"],
      meta: catalogEntry("lakera-guard"),
      catalogName: "lakera-guard",
      form: {
        enabled: true,
        action: "block",
        threshold: 0.9,
        fields: { api_key: "", endpoint: "", categories: ["jailbreak"] },
      },
    });
    expect(body).toEqual({
      name: "lakera-guard",
      config: {
        name: "lakera-guard",
        action: "block",
        threshold: 0.9,
        enabled: true,
        config: {
          provider: "lakera",
          api_key: "__encrypted__",
          categories: ["jailbreak"],
        },
        _policy: "policy-1",
      },
    });
  });

  it("names a new built-in rule by its gateway name and adds no provider", () => {
    const body = ruleBodyForSave({
      saved: DEMO,
      check: null,
      meta: catalogEntry("content-moderation"),
      catalogName: "content-moderation",
      form: { enabled: true, action: "warn", threshold: 0.8, fields: {} },
    });
    expect(body).toEqual({
      name: "content-moderation",
      config: {
        name: "content-moderation",
        mode: "sync",
        action: "warn",
        threshold: 0.8,
        enabled: true,
        config: {},
      },
    });
  });
});

// A block by a check on the model's answer refuses the answer with a 403
// (a stream ends with the guardrail's error), so Block works on either
// stage; only pii-detection can also mask (guardrail-check-actions.json).
describe("actions", () => {
  it("offers Block on either stage, with what each action does", () => {
    expect(actionHelp("block")).toBe(ACTION_HELP.block.help);
    expect(actionHelp("warn")).toBe(ACTION_HELP.warn.help);
    expect(actionHelp("log")).toBe(ACTION_HELP.log.help);
    expect(defaultActionFor()).toBe("block");
    const leak = catalogEntry("data-leakage-prevention");
    expect(formValuesFor(leak, null, actionOf).action).toBe("block");
    expect(
      formValuesFor(leak, { action: "warn", enabled: true }, actionOf).action,
    ).toBe("warn");
  });

  it("offers mask only on a check that can rewrite the request", () => {
    expect(actionChoices("pii-detection").map((c) => c.value)).toEqual([
      "block",
      "warn",
      "mask",
      "log",
    ]);
    expect(actionChoices("pii-detector").map((c) => c.value)).toContain("mask");
    expect(actionChoices("prompt-injection").map((c) => c.value)).toEqual([
      "block",
      "warn",
      "log",
    ]);
  });

  it("keeps a stored mask on a check that cannot take it, listed as running as log", () => {
    const choices = actionChoices("prompt-injection", "mask");
    expect(choices.at(-1)).toEqual({
      value: "mask",
      disabled: true,
      label: "Mask (runs as Log)",
    });
    expect(runsAsLog("prompt-injection", "mask")).toBe(true);
    expect(runsAsLog("pii-detection", "mask")).toBe(false);
  });

  it("adds no stage to a new check on the answer: the gateway runs it where its type runs", () => {
    const body = ruleBodyForSave({
      saved: DEMO,
      check: null,
      meta: catalogEntry("data-leakage-prevention"),
      catalogName: "data-leakage-prevention",
      form: { enabled: true, action: "warn", threshold: 0.8, fields: {} },
    });
    expect(body.config.action).toBe("warn");
    expect(body.config).not.toHaveProperty("stage");
  });
});

describe("a section stored as a checks map", () => {
  const check = { enabled: true, action: "mask", confidence_threshold: 0.6 };
  const meta = catalogEntry("pii-detection");
  const form = (over) => ({
    enabled: true,
    action: "mask",
    threshold: 0.6,
    fields: {},
    ...over,
  });

  it("sends only the keys the form changed, by the map's names", () => {
    expect(
      mapEditForSave({
        check,
        meta,
        catalogName: "pii-detection",
        form: form({ threshold: 0.4 }),
      }),
    ).toEqual({
      name: "pii-detection",
      config: { confidence_threshold: 0.4 },
    });
    expect(
      mapEditForSave({
        check: { enabled: true },
        meta: catalogEntry("data-leakage-prevention"),
        catalogName: "data-leakage-prevention",
        form: form({ action: "block", threshold: 0 }),
      }),
    ).toEqual({ name: "data-leakage-prevention", config: { action: "block" } });
  });

  it("sends nothing when the form changed nothing", () => {
    expect(
      mapEditForSave({
        check,
        meta,
        catalogName: "pii-detection",
        form: form(),
      }),
    ).toBeNull();
  });
});

describe("naming a rule the gateway logged", () => {
  it("uses the catalog name, maps legacy ids, and keeps an unknown id", () => {
    expect(ruleLabel("keyword-blocklist")).toBe("Keyword Blocklist");
    expect(ruleLabel("pii-detector")).toBe("PII Detection");
    expect(ruleLabel("acme-webhook")).toBe("acme-webhook");
    expect(ruleLabel("")).toBe("");
  });
});
