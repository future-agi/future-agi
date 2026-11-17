import { describe, expect, it } from "vitest";

import { EXAMPLES } from "../examples";

// The gateway's secret patterns, copied verbatim from
// agentcc-gateway/internal/guardrails/secrets/secrets.go defaultPatterns()
// (RE2's inline (?i) becomes the JS i flag). If those change, change these.
const GATEWAY_SECRET_PATTERNS = {
  openai_key: /\bsk-[A-Za-z0-9]{20,}\b/,
  password_assign: /(?:password|passwd|pwd|secret)\s*[=:]\s*[^\s,;]{8,}/i,
};

const preset = (label) => EXAMPLES.find((item) => item.label === label)?.value;

describe("Test tab presets", () => {
  it("has a Secret preset that Secret Detection really matches, with an obviously fake value", () => {
    const text = preset("Secret");
    expect(text).toBeTruthy();
    expect(GATEWAY_SECRET_PATTERNS.openai_key.test(text)).toBe(true);
    expect(GATEWAY_SECRET_PATTERNS.password_assign.test(text)).toBe(true);
    // Never something that could pass for a real key.
    expect(text).toMatch(/EXAMPLE/);
  });

  it("has a Safe prompt that no secret pattern matches", () => {
    const text = preset("Safe prompt");
    Object.values(GATEWAY_SECRET_PATTERNS).forEach((pattern) =>
      expect(pattern.test(text)).toBe(false),
    );
  });
});
