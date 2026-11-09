import { describe, expect, it } from "vitest";
import { catalogEntry, needsSetup } from "../index";
import { actionOf, normalizeGuardrails, ruleBodyForSave } from "../rule-model";
import { formValuesFor, formIsComplete } from "../form-model";

const meta = catalogEntry("tool-permissions");

describe("Tool Permissions policy mode namespace", () => {
  it.each(
    ["allowlist", "denylist", "audit"].flatMap((mode) =>
      ["sync", "async"].map((execution) => [mode, execution]),
    ),
  )("round trips %s with execution %s", (mode, execution) => {
    const rule = {
      name: meta.name,
      mode: execution,
      enabled: true,
      action: "block",
      threshold: 0.8,
      config: { provider: "tool_permission", mode, tools: "qa_blocked_*" },
    };
    const saved = { rules: [rule] };
    const check = normalizeGuardrails(saved).checks[meta.name];
    const form = formValuesFor(meta, check, actionOf);
    expect(form.fields.mode).toBe(mode);
    expect(formIsComplete(meta, form)).toBe(true);
    expect(needsSetup(meta, check)).toBe(false);
    const body = ruleBodyForSave({
      saved,
      check,
      meta,
      catalogName: meta.name,
      form,
    });
    expect(body.config).toEqual(rule);
  });

  it.each(["allowlist", "denylist", "audit"])(
    "retains valid legacy top-level policy %s",
    (mode) => {
      expect(
        formValuesFor(meta, { mode, tools: "qa_*" }, actionOf).fields.mode,
      ).toBe(mode);
    },
  );

  it.each(["sync", "async", undefined])(
    "never uses an outer execution value %s as policy",
    (mode) => {
      expect(
        formValuesFor(meta, { mode, config: { tools: "qa_*" } }, actionOf)
          .fields.mode,
      ).toBeUndefined();
    },
  );

  it("does not guess the intended policy for a corrupted explicit value", () => {
    const check = { mode: "sync", config: { mode: "sync", tools: "qa_*" } };
    const form = formValuesFor(meta, check, actionOf);
    expect(form.fields.mode).toBe("sync");
    expect(formIsComplete(meta, form)).toBe(false);
    expect(needsSetup(meta, check)).toBe(true);
  });

  it("preserves unrelated legacy provider-field precedence", () => {
    const provider = catalogEntry("presidio-pii");
    const check = {
      endpoint: "http://localhost:8001",
      config: { endpoint: "http://localhost:8002" },
    };
    expect(formValuesFor(provider, check, actionOf).fields.endpoint).toBe(
      check.endpoint,
    );
  });
});
