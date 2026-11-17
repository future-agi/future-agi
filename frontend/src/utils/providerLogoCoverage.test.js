import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { localProviderLogo, GENERIC_PROVIDER_LOGO } from "./localProviderLogo";

// Every identity model_hub's ProviderLogoUrls knows, including Enum aliases
// that share artwork, with the path the backend returns for it.
const choices = fs.readFileSync(
  path.resolve("../futureagi/model_hub/models/choices.py"),
  "utf8",
);
const block = choices
  .split("class ProviderLogoUrls(Enum):")[1]
  .split("    @classmethod")[0];
const backend = [
  ...block.matchAll(/^ {4}([A-Z][A-Z0-9_]+)\s*=\s*\(?\s*"([^"]+)"/gm),
].map((m) => ({ provider: m[1], url: m[2] }));

describe("complete local provider artwork", () => {
  it("draws, for every backend identity, the same-origin path the backend returns", () => {
    expect(backend.length).toBeGreaterThanOrEqual(55);
    for (const { provider, url } of backend) {
      for (const spelling of [
        provider,
        provider.toLowerCase(),
        provider.toLowerCase().replace(/_/g, "-"),
      ]) {
        const drawn = localProviderLogo({
          provider: spelling,
          logo_url: "https://unexpected.invalid/logo.png",
        });
        expect(drawn, spelling).not.toBe(GENERIC_PROVIDER_LOGO);
        expect(drawn, spelling).toBe(url);
      }
      expect(url, provider).toMatch(/^\/(?!\/)[^+%\s?#]+$/);
      expect(fs.existsSync(path.join("public", url)), provider).toBe(true);
    }
  });
});
