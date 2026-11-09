import React from "react";
import { it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ObserveInstruments from "./ObserveInstuments";

vi.mock("./InstructionCodeCopy", () => ({ default: () => null }));
it("ignores remote telemetry-provider logos in realistic API rows", () => {
  render(
    <ObserveInstruments
      isSuccess
      languageTab="python"
      onLanguageChange={vi.fn()}
      data={{
        openai: {
          name: "OpenAI",
          logo: "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openai-icon.png",
          Python: { code: "pass" },
        },
        custom: {
          name: "Unknown provider",
          logo: "https://fi-content-dev.s3.ap-south-1.amazonaws.com/custom.png",
          Python: { code: "pass" },
        },
      }}
    />,
  );
  expect(screen.getByAltText("OpenAI Logo")).toHaveAttribute(
    "src",
    "/icons/settings/openai.png",
  );
  expect(screen.getByAltText("Unknown provider Logo")).toHaveAttribute(
    "src",
    "/assets/icons/settings/AIProviders.svg",
  );
  expect(document.querySelector('img[src^="http"], img[src^="//"]')).toBeNull();
});
