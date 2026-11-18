import { describe, expect, it } from "vitest";

import { shouldTriggerEmbed } from "../EvalGroundTruthTab";

describe("shouldTriggerEmbed", () => {
  const base = {
    enabled: true,
    mappingDirty: false,
    embeddingsReady: true,
    hasOnEmbed: true,
  };

  it("returns false when GT is disabled, even if mapping is dirty", () => {
    expect(
      shouldTriggerEmbed({ ...base, enabled: false, mappingDirty: true }),
    ).toBe(false);
  });

  it("returns false when GT is disabled and embeddings are not ready", () => {
    expect(
      shouldTriggerEmbed({
        ...base,
        enabled: false,
        embeddingsReady: false,
      }),
    ).toBe(false);
  });

  it("returns true when enabled and mapping just changed", () => {
    expect(shouldTriggerEmbed({ ...base, mappingDirty: true })).toBe(true);
  });

  it("returns true when enabled and embeddings are not yet ready", () => {
    expect(
      shouldTriggerEmbed({ ...base, embeddingsReady: false }),
    ).toBe(true);
  });

  it("returns false when enabled, mapping clean, embeddings ready", () => {
    expect(shouldTriggerEmbed(base)).toBe(false);
  });

  it("returns false when no onEmbed callback is wired", () => {
    expect(
      shouldTriggerEmbed({ ...base, mappingDirty: true, hasOnEmbed: false }),
    ).toBe(false);
  });
});
