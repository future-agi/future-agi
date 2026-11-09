import { describe, expect, it } from "vitest";
import { mcpGuardrailRows } from "./storedGuardrails";
describe("stored MCP policy summaries", () => {
  it.each(["warn", "log"])("keeps %s distinct and changes no stored bytes", (action) => {
    const saved = { action, custom_patterns: ["private-pattern"], tool_rate_limits: { search: 12 } };
    const before = JSON.stringify(saved);
    const rows = mcpGuardrailRows(saved);
    expect(rows).toContainEqual({ label: "Action (stored only)", value: action === "warn" ? "Warn" : "Log" });
    expect(rows).toContainEqual({ label: "Custom injection patterns (stored only)", value: "private-pattern", mono: true });
    expect(rows).toContainEqual({ label: "Tool limits", value: "search: 12 per minute", mono: true });
    expect(JSON.stringify(saved)).toBe(before);
  });
});
