import { describe, expect, it } from "vitest";
import { formFromEntry, virtualServerPatch } from "./virtualServers";
import { limitsPatch, limitsRows } from "./toolLimits";

describe("MCP tool limits", () => {
  it("starts off without inventing limits", () => {
    expect(limitsRows()).toEqual([]);
    expect(
      virtualServerPatch(formFromEntry(null, "tools"), null).virtual_servers
        .tools,
    ).not.toHaveProperty("budgets");
  });
  it("saves zero as a blocking ceiling and blank as disabled", () => {
    expect(
      limitsPatch("", "0", [{ pattern: "mock/*", rpm: "3", calls: "0" }]),
    ).toEqual({
      rpm_per_key: null,
      max_tool_calls_per_run: 0,
      per_tool: { "mock/*": { rpm: 3, max_calls_per_run: 0 } },
    });
  });
  it("deletes removed rules through merge patch without dropping dollar fields", () => {
    expect(
      limitsPatch("", "", [], { per_tool: { "mock/*": { rpm: 10 } } }),
    ).toEqual({
      rpm_per_key: null,
      max_tool_calls_per_run: null,
      per_tool: { "mock/*": null },
    });
  });
  it.each(["-1", "1.5", "nan", "1e99"])(
    "rejects invalid ceiling %s",
    (value) => {
      expect(() => limitsPatch(value, "", [])).toThrow();
    },
  );
  it("rejects duplicate tool patterns instead of silently replacing a rule", () => {
    expect(() =>
      limitsPatch("", "", [
        { pattern: "a/*", rpm: "1" },
        { pattern: "a/*", rpm: "2" },
      ]),
    ).toThrow();
  });
});
