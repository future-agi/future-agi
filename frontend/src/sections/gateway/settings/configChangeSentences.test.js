import { describe, expect, it } from "vitest";

import { changeSentences, sectionLabel } from "./configChangeSentences";

const section = (parts) => ({ added: {}, removed: {}, changed: {}, ...parts });
const texts = (name, parts) =>
  changeSentences(name, section(parts)).map((line) => line.text);

describe("the Activate dialog's change sentences", () => {
  it("says a changed limit with its unit once, after both numbers", () => {
    expect(
      texts("rate_limiting", {
        changed: { global_rpm: { old: 60, new: 120 } },
      }),
    ).toEqual(["Rate limit: 60 → 120 requests per minute"]);
  });

  it("names 0 as unlimited, with the unit on the number that has one", () => {
    expect(
      texts("rate_limiting", {
        changed: { per_key_tpm: { old: 0, new: 50000 } },
      }),
    ).toEqual(["Token limit per key: unlimited → 50,000 tokens per minute"]);
  });

  it("says a switch turned on or off", () => {
    expect(
      texts("rate_limiting", {
        changed: { enabled: { old: false, new: true } },
      }),
    ).toEqual(["Rate limiting: turned on"]);
    expect(texts("cache", { added: { enabled: false } })).toEqual([
      "Response cache: turned off",
    ]);
  });

  it("says what was set and what was removed", () => {
    expect(
      texts("cache", {
        added: { default_ttl: "10m" },
        removed: { max_entries: 500 },
      }),
    ).toEqual(["Cache lifetime: set to 10m", "Max entries: removed (was 500)"]);
  });

  it("never writes the raw +, - or ~ markers", () => {
    const lines = texts("routing", {
      added: { strategy: "cost" },
      removed: { auto: { preference: "cost" } },
      changed: { failover: { old: { enabled: true }, new: {} } },
    });
    expect(lines).toEqual([
      "Routing strategy: set to cost",
      "Auto: removed (was 1 setting)",
      "Failover: 1 setting → none",
    ]);
    for (const line of lines) expect(line).not.toMatch(/^[+~-] /);
  });

  it("keeps the detail of a nested value, masked, under its sentence", () => {
    const [line] = changeSentences(
      "cache",
      section({
        added: { redis: { address: "redis:6379", password: "SECRET-VALUE" } },
      }),
    );
    expect(line.text).toBe("Redis: set to 2 settings");
    const detail = line.detail.join(" ");
    expect(detail).toContain("redis:6379");
    expect(detail).not.toContain("SECRET-VALUE");
  });

  it("lists a short list of plain values in the sentence", () => {
    expect(
      texts("routing", {
        changed: {
          model_fallbacks: { old: ["gpt-4o"], new: ["gpt-4o", "claude-3"] },
        },
      }),
    ).toEqual(["Fallback chains: gpt-4o → gpt-4o, claude-3"]);
  });

  it("names a section as the settings page does, and an unknown one readably", () => {
    expect(sectionLabel("rate_limiting")).toBe("Rate limiting");
    expect(sectionLabel("ip_acl")).toBe("IP access list");
    expect(sectionLabel("managed_models")).toBe("Future AGI credits");
    expect(sectionLabel("some_new_section")).toBe("Some new section");
  });
});
