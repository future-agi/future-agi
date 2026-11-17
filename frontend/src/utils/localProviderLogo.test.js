import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { describe, it, expect } from "vitest";
import {
  localProviderLogo,
  PROVIDER_LOGOS,
  GENERIC_PROVIDER_LOGO,
} from "./localProviderLogo";
import { normalizeModelOption } from "../components/custom-model-dropdown/common";
import { normalizeProviderStatus } from "../components/custom-model-dropdown/KeysHelper";

const remoteLogo =
  "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openai-icon.png";
describe("provider artwork is selected by identity, never logo_url", () => {
  it("maps realistic backend snake_case and picker camelCase rows locally", () => {
    for (const normalize of [normalizeModelOption, normalizeProviderStatus]) {
      for (const supplied of [
        { logo_url: remoteLogo },
        { logoUrl: remoteLogo },
      ]) {
        const row = normalize({
          provider: "openai",
          model_name: "gpt-5.5",
          ...supplied,
        });
        expect(row.logoUrl).toBe("/icons/settings/openai.png");
        expect(row.logo_url).toBe("/icons/settings/openai.png");
      }
    }
  });
  it("never infers identity from an attacker-controlled URL", () => {
    expect(
      localProviderLogo({ provider: "unknown", logo_url: remoteLogo }),
    ).toBe(GENERIC_PROVIDER_LOGO);
    expect(localProviderLogo(remoteLogo)).toBe(GENERIC_PROVIDER_LOGO);
    expect(
      localProviderLogo({
        providers: "OpenAI",
        logo_url: "https://unexpected.example/logo.svg",
      }),
    ).toBe(PROVIDER_LOGOS.openai);
    expect(localProviderLogo({ providers: ["azure"] })).toBe(
      PROVIDER_LOGOS.azure,
    );
    for (const identity of [
      undefined,
      "__proto__",
      "constructor",
      "https://openai",
      {},
    ])
      expect(localProviderLogo(identity)).toBe(GENERIC_PROVIDER_LOGO);
  });
  it("every selected asset exists locally", () => {
    for (const url of new Set([
      GENERIC_PROVIDER_LOGO,
      ...Object.values(PROVIDER_LOGOS),
    ])) {
      expect(url.startsWith("/") && !url.startsWith("//")).toBe(true);
      expect(fs.existsSync(path.join(process.cwd(), "public", url))).toBe(true);
    }
  });
});
