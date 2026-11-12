import { describe, expect, it } from "vitest";

import { keyPrefixLabel, matchesKeySearch } from "../key-format";

// The backend now stores the first and last four
// characters of a key's random part ("sk-agentcc-4f2a…9c1d"), so two keys
// read apart. Keys made before keep the gateway's 12-character prefix.
describe("keyPrefixLabel", () => {
  it("shows a two-ended prefix as stored, with no extra dots", () => {
    expect(keyPrefixLabel("sk-agentcc-4f2a…9c1d")).toBe("sk-agentcc-4f2a…9c1d");
  });

  it("keeps an older key's prefix as the gateway cut it", () => {
    expect(keyPrefixLabel("sk-agentcc-4...")).toBe("sk-agentcc-4...");
    expect(keyPrefixLabel("pk-test")).toBe("pk-test...");
  });

  it("reads the empty mark when there is no prefix", () => {
    expect(keyPrefixLabel("")).toBe("—");
    expect(keyPrefixLabel(null)).toBe("—");
  });
});

describe("matchesKeySearch", () => {
  it("finds a key by the last four characters its prefix shows", () => {
    const key = { name: "support", keyPrefix: "sk-agentcc-4f2a…9c1d" };
    expect(matchesKeySearch(key, "9c1d")).toBe(true);
    expect(matchesKeySearch(key, "b7e3")).toBe(false);
  });
});
