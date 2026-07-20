import { describe, it, expect } from "vitest";
import {
  canonicalKeys,
  canonicalEntries,
  canonicalValues,
} from "../canonicalKeys";
import { canonicalKeys as legacyCanonicalKeys } from "../utils";

// Helper that simulates a legacy object containing both canonical keys and
// old alias keys so iteration sees both.
const withAliases = (obj) => {
  const out = { ...obj };
  Object.keys(obj).forEach((k) => {
    if (k.includes("_")) {
      const camel = k.replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase());
      if (camel !== k && !(camel in out)) {
        out[camel] = obj[k];
      }
    }
  });
  return out;
};

describe("canonicalKeys / canonicalEntries / canonicalValues Unit", () => {
  it("returns only snake_case keys when both forms exist", () => {
    const obj = withAliases({ user_id: 1, total_rows: 10 });
    // Sanity check: the aliased object has doubled keys
    expect(Object.keys(obj)).toEqual([
      "user_id",
      "total_rows",
      "userId",
      "totalRows",
    ]);
    expect(canonicalKeys(obj).sort()).toEqual(["total_rows", "user_id"]);
  });

  it("preserves the legacy utils export path", () => {
    expect(legacyCanonicalKeys(withAliases({ user_id: 1 }))).toEqual([
      "user_id",
    ]);
  });

  it("keeps genuine camelCase keys that have no snake_case twin", () => {
    const obj = { id: 1, name: "x", someField: "y" };
    expect(canonicalKeys(obj).sort()).toEqual(["id", "name", "someField"]);
  });

  it("keeps snake_case keys that have no alias created", () => {
    const obj = { only_snake: 1 };
    expect(canonicalKeys(obj)).toEqual(["only_snake"]);
  });

  it("handles mixed real and aliased keys", () => {
    const obj = withAliases({
      id: "abc",
      user_id: 1,
      metadata: { foo: "bar" },
    });
    expect(canonicalKeys(obj).sort()).toEqual(["id", "metadata", "user_id"]);
  });

  it("canonicalEntries returns matching [key, value] pairs", () => {
    const obj = withAliases({ user_id: 1, total_rows: 10 });
    const entries = canonicalEntries(obj).sort(([a], [b]) =>
      a.localeCompare(b),
    );
    expect(entries).toEqual([
      ["total_rows", 10],
      ["user_id", 1],
    ]);
  });

  it("canonicalValues returns the de-duped values", () => {
    const obj = withAliases({ user_id: 1, total_rows: 10 });
    expect(canonicalValues(obj).sort()).toEqual([1, 10]);
  });

  it("returns [] for null/undefined/primitive inputs", () => {
    expect(canonicalKeys(null)).toEqual([]);
    expect(canonicalKeys(undefined)).toEqual([]);
    expect(canonicalKeys("hello")).toEqual([]);
    expect(canonicalEntries(null)).toEqual([]);
    expect(canonicalValues(null)).toEqual([]);
  });

  it("does not corrupt arrays (returns only numeric keys)", () => {
    const arr = [1, 2, 3];
    // Arrays: Object.keys returns ["0","1","2"] — none contain "_",
    // none have an aliased twin, so all three survive.
    expect(canonicalKeys(arr)).toEqual(["0", "1", "2"]);
  });

  it("drops alias even when snake key has multiple underscores", () => {
    const obj = withAliases({ total_token_count: 42 });
    expect(canonicalKeys(obj)).toEqual(["total_token_count"]);
  });

  it("drops alias when snake key has digit separators", () => {
    // `tone_17_apr_2026` → alias `tone17Apr2026`. Reversing that alias
    // back to snake by regex alone can't recover the `_` before digits,
    // which historically let the alias slip through. The forward-mapping
    // implementation handles it correctly.
    const obj = withAliases({ tone_17_apr_2026: { neutral: 10 } });
    expect(Object.keys(obj)).toContain("tone17Apr2026");
    expect(canonicalKeys(obj)).toEqual(["tone_17_apr_2026"]);
  });

  it("preserves the legacy contract by hiding camelCase twins by key", () => {
    const metadata = {
      user_id: "snake-value",
      userId: "camel-value",
      nested: {
        inner_key: 1,
        innerKey: 2,
      },
      events: [
        {
          request_id: "snake-request",
          requestId: "camel-request",
        },
      ],
    };

    expect(canonicalKeys(metadata).sort()).toEqual([
      "events",
      "nested",
      "user_id",
    ]);
    expect(canonicalKeys(metadata.nested)).toEqual(["inner_key"]);
    expect(canonicalKeys(metadata.events[0])).toEqual(["request_id"]);
  });

  it("preserves __proto__ data while filtering generated aliases", () => {
    const metadata = JSON.parse(
      '{"__proto__":{"polluted":true},"safe_key":1,"safeKey":1,"nested":{"__proto__":{"nested":true},"inner_key":2,"innerKey":2}}',
    );

    const entries = canonicalEntries(metadata);
    const nestedEntries = canonicalEntries(metadata.nested);

    expect(Object.prototype.polluted).toBeUndefined();
    expect(Object.prototype.hasOwnProperty.call(metadata, "__proto__")).toBe(
      true,
    );
    expect(entries.map(([key]) => key)).toEqual([
      "__proto__",
      "safe_key",
      "nested",
    ]);
    expect(nestedEntries.map(([key]) => key)).toEqual([
      "__proto__",
      "inner_key",
    ]);
    expect(
      entries.some(([key, value]) => key === "__proto__" && value.polluted),
    ).toBe(true);
    expect(
      nestedEntries.some(([key, value]) => key === "__proto__" && value.nested),
    ).toBe(true);
  });

  it("drops serialized object and array aliases by key at the current level", () => {
    const metadata = JSON.parse(
      '{"object_key":{"a":1},"objectKey":{"a":1},"array_key":[{"b":2}],"arrayKey":[{"b":2}]}',
    );

    expect(canonicalKeys(metadata)).toEqual(["object_key", "array_key"]);
  });
});
