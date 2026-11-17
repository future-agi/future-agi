import { describe, expect, it } from "vitest";
import { catalogEntry, needsSetup } from "../index";
import {
  actionOf,
  draftCheck,
  mapEditForSave,
  normalizeGuardrails,
  ruleBodyForSave,
} from "../rule-model";
import { formIsComplete, formToConfig, formValuesFor } from "../form-model";

const tool = catalogEntry("tool-permissions");
const cases = [
  ["null", { mode: null }],
  ["empty", { mode: "" }],
  ["missing", {}],
  ["invalid", { mode: "sync" }],
];

function ruleFor(meta, settings) {
  return {
    name: meta.name,
    mode: "sync",
    enabled: true,
    action: "block",
    threshold: 0.8,
    config: { provider: meta.provider, ...settings },
  };
}

describe("stored guardrail settings are not silently repaired", () => {
  it.each(cases)(
    "preserves %s mode and field presence through every save path",
    (_, mode) => {
      const rule = ruleFor(tool, { ...mode, tools: "qa_blocked_*" });
      const saved = { rules: [rule] };
      const check = normalizeGuardrails(saved).checks[tool.name];
      const form = formValuesFor(tool, check, actionOf);
      expect(form.fields.mode).toBe(rule.config.mode);
      const omitted = !Object.hasOwn(mode, "mode");
      expect(formIsComplete(tool, form)).toBe(omitted);
      expect(needsSetup(tool, check)).toBe(!omitted);
      const posted = ruleBodyForSave({
        saved,
        check,
        meta: tool,
        catalogName: tool.name,
        form,
      });
      expect(JSON.stringify(posted.config)).toBe(JSON.stringify(rule));
      expect(JSON.stringify(draftCheck(tool, form, check).config)).toBe(
        JSON.stringify(rule.config),
      );
      expect(
        mapEditForSave({ check, meta: tool, catalogName: tool.name, form }),
      ).toBeNull();
      expect(JSON.stringify(formToConfig(tool, form.fields, rule.config))).toBe(
        JSON.stringify(rule.config),
      );
    },
  );

  it.each(cases)(
    "changes %s mode only after an explicit selection",
    (_, mode) => {
      const rule = ruleFor(tool, { ...mode, tools: "qa_blocked_*" });
      const saved = { rules: [rule] };
      const check = normalizeGuardrails(saved).checks[tool.name];
      const initial = formValuesFor(tool, check, actionOf);
      const form = {
        ...initial,
        fields: { ...initial.fields, mode: "denylist" },
      };
      expect(formIsComplete(tool, form)).toBe(true);
      expect(
        ruleBodyForSave({
          saved,
          check,
          meta: tool,
          catalogName: tool.name,
          form,
        }).config.config,
      ).toEqual({ ...rule.config, mode: "denylist" });
    },
  );

  it("still supplies the default when adding a new rule", () => {
    expect(formValuesFor(tool, null, actionOf).fields.mode).toBe("denylist");
  });

  it.each([true, false, "true", "false"])(
    "accepts and preserves MCP Security validate_inputs=%j",
    (validate_inputs) => {
      const meta = catalogEntry("mcp-security");
      const rule = ruleFor(meta, {
        validate_inputs,
        max_calls_per_request: 10,
      });
      const saved = { rules: [rule] };
      const check = normalizeGuardrails(saved).checks[meta.name];
      const form = formValuesFor(meta, check, actionOf);
      expect(form.fields.validate_inputs).toBe(validate_inputs);
      expect(formIsComplete(meta, form)).toBe(true);
      expect(needsSetup(meta, check)).toBe(false);
      expect(
        JSON.stringify(
          ruleBodyForSave({ saved, check, meta, catalogName: meta.name, form })
            .config,
        ),
      ).toBe(JSON.stringify(rule));
    },
  );

  it.each([true, false, 0, 1, {}, []])(
    "does not coerce invalid Tool Permissions mode %j",
    (mode) => {
      const check = { config: { mode, tools: "qa_*" } };
      expect(formIsComplete(tool, formValuesFor(tool, check, actionOf))).toBe(
        false,
      );
      expect(needsSetup(tool, check)).toBe(true);
    },
  );
});
