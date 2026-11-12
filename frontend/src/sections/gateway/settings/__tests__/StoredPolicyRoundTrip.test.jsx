import { describe, expect, it, vi } from "vitest";
import { catalogEntry } from "../../guardrails/catalog";
import {
  formValuesFor,
  mapEditForSave,
  normalizeGuardrails,
  ruleBodyForSave,
  storedActionOf,
} from "../../guardrails/catalog/rule-model";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));

const storedConfig = {
  allowed_servers: " stored-only ",
  blocked_tools: " db_write ",
  validate_inputs: " true ",
  custom_patterns: ["ROUNDTRIP_MARKER"],
  tool_rate_limits: { lookup: 3 },
  future_setting: { keep: [null, " untouched "] },
};
const storedRule = {
  name: "mcp-security",
  enabled: false,
  config: storedConfig,
  future_rule_key: " untouched ",
};
const otherRule = {
  name: "tool-permissions",
  enabled: true,
  config: { mode: "denylist", tools: " file_* " },
};

function drawerSave(rule, edit = (form) => form) {
  const saved = { rules: [rule] };
  const check = normalizeGuardrails(saved).checks[rule.name];
  const meta = catalogEntry(rule.name);
  const form = edit(formValuesFor(meta, check, storedActionOf));
  return ruleBodyForSave({ saved, check, meta, catalogName: rule.name, form });
}

describe("stored drawer fields are patched, not normalized again", () => {
  it.each([
    storedRule,
    { ...storedRule, config: {} },
    { name: "mcp-security", enabled: false },
    { ...otherRule, enabled: false },
  ])("preserves every stored key and absence for $name", (rule) => {
    expect(JSON.stringify(drawerSave(rule).config)).toBe(JSON.stringify(rule));
    expect(
      drawerSave(rule, (form) => ({ ...form, enabled: true })).config,
    ).toEqual({
      ...rule,
      enabled: true,
    });
  });

  it("normalizes only the explicitly edited field", () => {
    const body = drawerSave(storedRule, (form) => ({
      ...form,
      fields: { ...form.fields, blocked_tools: " lookup " },
    }));
    expect(body.config).toEqual({
      ...storedRule,
      config: { ...storedConfig, blocked_tools: "lookup" },
    });
  });

  it("can clear a field without filling defaults or changing hidden policy", () => {
    const { blocked_tools: _removed, ...kept } = storedConfig;
    const body = drawerSave(storedRule, (form) => ({
      ...form,
      fields: { ...form.fields, blocked_tools: "" },
    }));
    expect(body.config).toEqual({ ...storedRule, config: kept });
  });

  it("keeps a string threshold and absent mode on an enabled-only save", () => {
    const rule = { ...storedRule, threshold: "1.00", action: "warn" };
    expect(
      drawerSave(rule, (form) => ({ ...form, enabled: true })).config,
    ).toEqual({
      ...rule,
      enabled: true,
    });
  });

  it("sends only enabled for a checks-map re-enable and nothing for a no-op", () => {
    const check = { enabled: false, config: storedConfig };
    const meta = catalogEntry("mcp-security");
    const form = formValuesFor(
      meta,
      check,
      (c) => storedActionOf(c, { asMap: true }),
      0,
    );
    const args = { check, meta, catalogName: "mcp-security", form };
    expect(mapEditForSave(args)).toBeNull();
    expect(
      mapEditForSave({ ...args, form: { ...form, enabled: true } }),
    ).toEqual({
      name: "mcp-security",
      config: { enabled: true },
    });
  });

  it("still initializes and normalizes a newly created rule", () => {
    const meta = catalogEntry("mcp-security");
    const form = formValuesFor(meta, null, storedActionOf);
    form.fields.blocked_tools = " lookup ";
    const body = ruleBodyForSave({
      saved: {},
      check: null,
      meta,
      catalogName: meta.name,
      form,
    });
    expect(body.config.config).toEqual({
      provider: "mcp_security",
      validate_inputs: "true",
      max_calls_per_request: 10,
      blocked_tools: "lookup",
    });
  });
});
