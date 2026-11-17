// OSS RC1 f036096425: packages/studio-protocol/openapi/omega-embed-1.json.
// Hash raw bytes, never parsed/re-serialized JSON: whitespace is contractual.
import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";

import fixtureBytes from "../omega-embed-1.json?raw";

const RC1_SHA256 =
  "b2c0ed231414ffa35f07213a29702f411f514d130ded8d0c9d189e91afa82438";
const matchesCanonical = (bytes) =>
  createHash("sha256").update(bytes).digest("hex") === RC1_SHA256;

const bytes = Buffer.from(fixtureBytes, "utf8");

describe("canonical omega-embed/1 fixture", () => {
  it("pins all 957 RC1 bytes to the OSS SHA-256", () => {
    expect(bytes.length).toBe(957);
    expect(matchesCanonical(bytes)).toBe(true);
  });

  it("rejects a one-byte change even when the JSON means the same thing", () => {
    const changed = Buffer.from(bytes);
    // Replace the first indentation space with a tab: still valid JSON.
    const offset = changed.indexOf(0x20);
    expect(offset).toBeGreaterThan(-1);
    changed[offset] = 0x09;
    expect(changed.length).toBe(bytes.length);
    expect(JSON.parse(changed.toString("utf8"))).toEqual(
      JSON.parse(bytes.toString("utf8")),
    );
    expect(matchesCanonical(changed)).toBe(false);
    expect(matchesCanonical(bytes)).toBe(true); // original was not mutated
  });
});
