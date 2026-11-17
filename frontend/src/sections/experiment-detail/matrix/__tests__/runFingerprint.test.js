// IT-R5 G6 P3: identical run headers side by side show their setup once.
import { describe, expect, it } from "vitest";
import { sameSetupAs } from "../runFingerprint";

describe("sameSetupAs", () => {
  it("points every repeat at the first identical header on its left", () => {
    const col = (runId, label, name, fingerprint) => ({
      runId,
      label,
      name,
      fingerprint,
    });
    const out = sameSetupAs([
      col("a", "#2E", "Codex CLI", "gpt-5-codex · Codex CLI"),
      col(
        "f",
        "#2F",
        "Support Agent v3.1",
        "gpt-4o-mini · Agent graph · 6 tools",
      ),
      col(
        "g",
        "#2G",
        "Support Agent v3.1",
        "gpt-4o-mini · Agent graph · 6 tools",
      ),
      col(
        "h",
        "#2H",
        "Support Agent v3.1",
        "gpt-4o-mini · Agent graph · 6 tools",
      ),
      col("i", "#2I", "Support Agent v3.1", "gpt-4o · Agent graph · 6 tools"),
    ]);
    expect(out).toEqual({ a: null, f: null, g: "#2F", h: "#2F", i: null });
  });
});
