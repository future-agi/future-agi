import { describe, expect, it } from "vitest";
import {
  extractVariables,
  extractVariablesFromMessages,
} from "../utils";

// The helper unions template variables across a single instructions string
// plus every message content in a multi-turn messages array. It's the FE
// counterpart to the widened BE gate: every LLM eval surface that needs to
// count variables in a multi-turn template goes through here.

describe("extractVariablesFromMessages", () => {
  describe("mustache mode (default)", () => {
    it("extracts from instructions only", () => {
      expect(
        extractVariablesFromMessages("Hello {{name}}", []),
      ).toEqual(["name"]);
    });

    it("extracts from a single message content", () => {
      expect(
        extractVariablesFromMessages("", [
          { role: "user", content: "Value is {{input}}" },
        ]),
      ).toEqual(["input"]);
    });

    it("unions variables across instructions + every message", () => {
      const result = extractVariablesFromMessages(
        "System uses {{criteria}}",
        [
          { role: "system", content: "System uses {{criteria}}" },
          { role: "user", content: "User uses {{input}}" },
          { role: "assistant", content: "Assistant uses {{expected}}" },
        ],
      );
      expect(result.sort()).toEqual(["criteria", "expected", "input"]);
    });

    it("deduplicates identical variables across turns", () => {
      const result = extractVariablesFromMessages(
        "{{shared}}",
        [
          { role: "user", content: "{{shared}}" },
          { role: "assistant", content: "{{shared}}" },
        ],
      );
      expect(result).toEqual(["shared"]);
    });

    it("returns empty array for empty inputs", () => {
      expect(extractVariablesFromMessages("", [])).toEqual([]);
      expect(extractVariablesFromMessages(null, null)).toEqual([]);
      expect(extractVariablesFromMessages(undefined, undefined)).toEqual([]);
    });

    it("handles missing / falsy content on messages", () => {
      const result = extractVariablesFromMessages(
        "{{a}}",
        [
          { role: "user" },
          { role: "assistant", content: null },
          { role: "user", content: "" },
          { role: "user", content: "{{b}}" },
        ],
      );
      expect(result.sort()).toEqual(["a", "b"]);
    });

    it("ignores messages that are not objects", () => {
      const result = extractVariablesFromMessages(
        "{{a}}",
        [null, undefined, "not-a-dict", { role: "user", content: "{{b}}" }],
      );
      expect(result.sort()).toEqual(["a", "b"]);
    });

    it("accepts a non-array messages value without throwing", () => {
      expect(
        extractVariablesFromMessages("{{x}}", "not-an-array"),
      ).toEqual(["x"]);
      expect(extractVariablesFromMessages("{{x}}", null)).toEqual(["x"]);
    });
  });

  describe("jinja mode", () => {
    it("delegates to extractVariables in jinja mode for each fragment", () => {
      // Baseline: extractVariables handles jinja parsing itself. This test
      // only proves the helper forwards the templateFormat argument, so a
      // jinja-only construct works consistently across turns.
      const jinjaContent = "{{ name | upper }}";
      const perFragment = extractVariables(jinjaContent, "jinja");
      const union = extractVariablesFromMessages(
        "",
        [{ role: "user", content: jinjaContent }],
        "jinja",
      );
      expect(union).toEqual(perFragment);
    });
  });

  describe("regression - the reported bug", () => {
    it("finds a variable that lives only in a User turn (System is plain text)", () => {
      // Before the fix, this returned [] because the caller only scanned
      // the System-derived instructions field. The FE gates then disabled
      // Save and Test on a template that was actually valid.
      const result = extractVariablesFromMessages("Judge the answer.", [
        { role: "system", content: "Judge the answer." },
        { role: "user", content: "Input: {{input}}" },
      ]);
      expect(result).toContain("input");
    });

    it("finds a variable that lives only in an Assistant turn", () => {
      const result = extractVariablesFromMessages("Judge the answer.", [
        { role: "system", content: "Judge the answer." },
        { role: "user", content: "See below." },
        { role: "assistant", content: "Expected: {{expected}}" },
      ]);
      expect(result).toContain("expected");
    });
  });
});
