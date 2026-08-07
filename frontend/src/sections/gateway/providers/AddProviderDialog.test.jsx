import { describe, expect, it } from "vitest";
import {
  GREENPT_ADD_PROVIDER_PRESET,
  GREENPT_SETTINGS_PROVIDER_OPTION,
} from "./providerPresetData";
import { parseTimeoutSeconds } from "./utils";

describe("parseTimeoutSeconds", () => {
  it("normalizes Gateway provider timeout text to integer seconds", () => {
    expect(parseTimeoutSeconds("45")).toBe(45);
    expect(parseTimeoutSeconds("45s")).toBe(45);
    expect(parseTimeoutSeconds("2m")).toBe(120);
    expect(parseTimeoutSeconds("1500ms")).toBe(2);
    expect(parseTimeoutSeconds("")).toBeNull();
    expect(parseTimeoutSeconds("soon")).toBeNull();
    expect(parseTimeoutSeconds("0s")).toBeNull();
  });
});

describe("provider presets", () => {
  it("exposes GreenPT through the OpenAI-compatible API", () => {
    expect(GREENPT_ADD_PROVIDER_PRESET).toEqual({
      label: "GreenPT",
      baseUrl: "https://api.greenpt.ai/v1",
      apiFormat: "openai",
      keyPlaceholder: "Enter your GreenPT API key",
      supportedFormats: ["openai"],
    });
  });

  it("exposes GreenPT in gateway settings", () => {
    expect(GREENPT_SETTINGS_PROVIDER_OPTION).toEqual({
      value: "greenpt",
      label: "GreenPT",
      baseUrl: "https://api.greenpt.ai",
    });
  });
});
