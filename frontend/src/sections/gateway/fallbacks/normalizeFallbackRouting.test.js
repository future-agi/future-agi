import { describe, expect, it } from "vitest";
import { buildConfigPatch } from "../hooks/useConfigPatch";
import { normalizeFallbackRouting } from "./normalizeFallbackRouting";

describe("normalizeFallbackRouting", () => {
  it("keeps canonical-only routing unchanged", () => {
    const routing = {
      model_fallbacks: { primary: ["backup"] },
      strategy: "cost",
    };
    expect(normalizeFallbackRouting(routing)).toBe(routing);
    expect(normalizeFallbackRouting({})).toEqual({});
  });

  it("collapses only the fallback alias without mutating stored routing", () => {
    const routing = Object.freeze({
      modelFallbacks: { primary: ["backup"] },
      modelTimeouts: { primary: "30s" },
      strategy: "cost",
    });
    expect(normalizeFallbackRouting(routing)).toEqual({
      model_fallbacks: { primary: ["backup"] },
      modelTimeouts: { primary: "30s" },
      strategy: "cost",
    });
    expect(routing).toHaveProperty("modelFallbacks");
  });

  it.each([{}, null])(
    "respects an explicitly empty canonical value (%j)",
    (value) => {
      const routing = {
        model_fallbacks: value,
        modelFallbacks: { shadowed: ["backup"] },
      };
      const before = normalizeFallbackRouting(routing);
      const after = normalizeFallbackRouting({
        ...routing,
        model_fallbacks: { added: ["backup"] },
      });
      expect(before).toEqual({ model_fallbacks: value });
      expect(
        buildConfigPatch({ routing: before }, { routing: after }, ["routing"]),
      ).toEqual({
        routing: { model_fallbacks: { added: ["backup"] } },
      });
    },
  );
});
