import { describe, expect, it } from "vitest";
import { CATALOG, catalogEntry } from "../index";
import { invalidSelectValue } from "../form-model";

// gateway_admin.GuardrailCheck.config is an open map. These adapters read
// strings, not local enums; catalogue options are suggestions, not a contract.
const openFields = [
  ["bedrock-guardrails", "region", "us-east-2"],
  ["presidio-pii", "language", "ko"],
  ["ibm-ai-detector", "criteria_id", "custom-risk"],
  ["grayswan-guard", "reasoning_mode", "provider-extension"],
  ["zscaler-guard", "cloud", "fixture.invalid"],
];

describe("select validation follows the runtime domain", () => {
  it.each(openFields)(
    "preserves %s %s outside suggestions",
    (name, key, value) => {
      const field = catalogEntry(name).fields.find((item) => item.key === key);
      expect(field.options).not.toContain(value);
      expect(invalidSelectValue(field, value)).toBe(false);
    },
  );

  it("keeps Tool Permissions policies and MCP booleans bounded", () => {
    const mode = catalogEntry("tool-permissions").fields.find(
      (field) => field.key === "mode",
    );
    const boolean = catalogEntry("mcp-security").fields.find(
      (field) => field.key === "validate_inputs",
    );
    expect(invalidSelectValue(mode, "sync")).toBe(true);
    expect(invalidSelectValue(boolean, "unknown")).toBe(true);
    [true, false, "true", "false"].forEach((value) => {
      expect(invalidSelectValue(boolean, value)).toBe(false);
    });
  });

  it("accounts for every catalogue select domain", () => {
    const fields = CATALOG.flatMap((meta) =>
      (meta.fields || [])
        .filter((field) => field.type === "select")
        .map((field) => `${meta.name}.${field.key}`),
    );
    expect(fields.sort()).toEqual(
      [
        ...openFields.map(([name, key]) => `${name}.${key}`),
        "tool-permissions.mode",
        "mcp-security.validate_inputs",
      ].sort(),
    );
  });
});
