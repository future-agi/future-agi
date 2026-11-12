// W6 C2: a model card shows its "Model parameters" button only
// when the model's parameter list has something to set. A TTS / STT / Image
// model answers empty lists (no LLM sampling sliders), so its card has no
// button that would open an empty popover; a list not loaded yet keeps it.
import { describe, expect, it } from "vitest";
import { offersModelParams } from "../modelCapabilities";

const EMPTY = {
  sliders: [],
  dropdowns: [],
  booleans: [],
  responseFormat: [],
  reasoning: { dropdowns: [], sliders: [] },
};

describe("offersModelParams", () => {
  it("an empty parameter list offers nothing (media models)", () => {
    expect(offersModelParams(EMPTY)).toBe(false);
    expect(offersModelParams({})).toBe(false);
  });
  it("any slider, toggle, dropdown or response format is something to set", () => {
    expect(
      offersModelParams({
        ...EMPTY,
        sliders: [{ label: "temperature", min: 0, max: 2, default: 1 }],
      }),
    ).toBe(true);
    expect(
      offersModelParams({ ...EMPTY, booleans: [{ label: "stream" }] }),
    ).toBe(true);
    expect(
      offersModelParams({ ...EMPTY, dropdowns: [{ label: "voice" }] }),
    ).toBe(true);
    expect(offersModelParams({ ...EMPTY, responseFormat: ["text"] })).toBe(
      true,
    );
  });
  it("reasoning lists alone do not count (the card has no reasoning toggle)", () => {
    expect(
      offersModelParams({
        ...EMPTY,
        reasoning: { dropdowns: [{ label: "effort" }], sliders: [] },
      }),
    ).toBe(false);
  });
  it("a list not loaded yet (or none) keeps the button", () => {
    expect(offersModelParams(undefined)).toBe(true);
    expect(offersModelParams(null)).toBe(true);
  });
});
