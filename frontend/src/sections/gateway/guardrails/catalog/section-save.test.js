import { describe, expect, it } from "vitest";
import { guardrailsForSave } from "../../hooks/useConfigPatch";
import { normalizeGuardrails } from "./rule-model";

const clone = (value) => structuredClone(value);
const KEEP = {
  name: "mcp-security",
  enabled: null,
  action: null,
  threshold: null,
  _policy: "policy-synthetic",
  future: { untouched: [null, " exact "] },
  config: { blocked_tools: " lookup " },
};

describe.each(["rules", "checks"])("stored %s array saves", (field) => {
  it.each(["enabled", "config", "confidence_threshold"])(
    "overlays only edited %s on the effective alias entry",
    (key) => {
      const entries = [
        clone(KEEP),
        { name: "pii-detector", enabled: true, threshold: 0.4 },
        {
          name: "pii-detection",
          enabled: false,
          threshold: null,
          config: { keep: " raw " },
        },
      ];
      const saved = { [field]: clone(entries) };
      const draft = normalizeGuardrails(saved);
      const value = {
        enabled: true,
        config: { keep: "changed" },
        confidence_threshold: 0.3,
      }[key];
      draft.checks["pii-detection"] = {
        ...draft.checks["pii-detection"],
        [key]: value,
      };
      entries[2][key === "confidence_threshold" ? "threshold" : key] = value;
      const after = guardrailsForSave(draft, saved);
      expect(JSON.stringify(after)).toBe(JSON.stringify({ [field]: entries }));
      expect(after[field][0]).toBe(saved[field][0]);
      expect(after[field][1]).toBe(saved[field][1]);
    },
  );

  it("removes every alias of the selected check without exposing an older duplicate", () => {
    const saved = {
      [field]: [
        clone(KEEP),
        { name: "pii-detector", enabled: true },
        { name: "pii-detection", enabled: false },
      ],
    };
    const draft = normalizeGuardrails(saved);
    delete draft.checks["pii-detection"];
    expect(guardrailsForSave(draft, saved)).toEqual({ [field]: [KEEP] });
  });

  it("appends a new check without normalizing a stored entry", () => {
    const saved = { [field]: [clone(KEEP)] };
    const draft = normalizeGuardrails(saved);
    draft.checks["keyword-blocklist"] = {
      enabled: true,
      action: "warn",
      confidence_threshold: 0.5,
      config: { words: ["fixture"] },
    };
    const after = guardrailsForSave(draft, saved);
    expect(after[field][0]).toBe(saved[field][0]);
    expect(after[field][1]).toEqual({
      name: "keyword-blocklist",
      enabled: true,
      action: "warn",
      threshold: 0.5,
      stage: "pre",
      mode: "sync",
      config: { words: ["fixture"] },
    });
  });
});

it.each([[], {}])(
  "keeps the empty checks carrier %j when rules are effective",
  (checks) => {
    const saved = { checks, rules: [clone(KEEP)] };
    const draft = normalizeGuardrails(saved);
    draft.checks["mcp-security"] = {
      ...draft.checks["mcp-security"],
      enabled: false,
    };
    expect(guardrailsForSave(draft, saved)).toEqual({
      checks,
      rules: [{ ...KEEP, enabled: false }],
    });
  },
);

it.each(["list", "map"])(
  "removing the last %s check cannot reactivate shadowed rules",
  (shape) => {
    const saved = {
      rules: [{ name: "keyword-blocklist", enabled: true }],
      checks:
        shape === "list" ? [clone(KEEP)] : { "mcp-security": clone(KEEP) },
    };
    const draft = { ...normalizeGuardrails(saved), checks: {} };
    expect(guardrailsForSave(draft, saved)).toEqual({
      rules: [],
      checks: shape === "list" ? [] : {},
    });
  },
);

// GL-1029-2: Allowed Servers is not offered on any form, and a stored value
// survives every whole-section save of another check, in every stored shape.
describe.each(["rules", "checks", "map"])(
  "a stored MCP Security Allowed Servers in %s",
  (shape) => {
    const MCP = {
      name: "mcp-security",
      enabled: true,
      threshold: 0.5,
      config: {
        allowed_servers: " walk-echo ",
        blocked_tools: "db_write",
        provider: "mcp_security",
      },
    };
    const KEYWORDS = {
      name: "keyword-blocklist",
      enabled: true,
      config: { words: ["zeus"] },
    };
    const sectionOf = (entries) =>
      shape === "map"
        ? {
            checks: Object.fromEntries(
              entries.map(({ name, ...entry }) => [name, entry]),
            ),
          }
        : { [shape]: entries };

    it("comes back byte for byte when another check is edited or removed", () => {
      for (const edit of ["toggle", "remove"]) {
        const saved = sectionOf(clone([MCP, KEYWORDS]));
        // A new draft object: a stored checks map normalizes to itself.
        const normalized = normalizeGuardrails(saved);
        const draft = { ...normalized, checks: { ...normalized.checks } };
        if (edit === "toggle") {
          draft.checks["keyword-blocklist"] = {
            ...draft.checks["keyword-blocklist"],
            enabled: false,
          };
        } else {
          delete draft.checks["keyword-blocklist"];
        }
        const after = guardrailsForSave(draft, saved);
        if (shape === "map") {
          expect(after.checks["mcp-security"]).toBe(
            saved.checks["mcp-security"],
          );
        } else {
          expect(after[shape][0]).toBe(saved[shape][0]);
        }
        const stored =
          shape === "map" ? after.checks["mcp-security"] : after[shape][0];
        expect(stored.config.allowed_servers).toBe(" walk-echo ");
        expect(JSON.stringify(stored.config)).toBe(JSON.stringify(MCP.config));
      }
    });
  },
);
