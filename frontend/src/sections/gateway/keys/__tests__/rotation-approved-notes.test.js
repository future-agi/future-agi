import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { ROTATE_REVOCATION_COPY } from "../rotate";
import rotateSource from "../rotate.js?raw";
import pageSource from "../KeyManagementSection.jsx?raw";
import approved from "./rotation-copy.activation.json";

const digest = (value) => createHash("sha256").update(value, "utf8").digest("hex");
const obsoleteDigests = new Set([
  "05dab393d326efec76596b9f674a7b4a911d3c2de4b704d3e31ee87742360557",
  "4132eb8466953a05f57cc2bdd1799b9406753de9aff865176c1b45d26025eb63",
  "b784425c0759032a8890a4a8f5f2fcff9c8b7df144c6dc9fdfad4e98d4b3c3f3",
]);

describe("activated outcome-note bytes", () => {
  it.each([
    [11, ROTATE_REVOCATION_COPY.local("${name}")],
    [12, ROTATE_REVOCATION_COPY.confirmed],
    [13, ROTATE_REVOCATION_COPY.pending],
  ])("retains exact activated row %s", (id, value) => {
    const row = approved.rows[`KEYS-ROTATE-${id}`];
    expect(value).toBe(row.afterExact);
    expect(digest(value)).toBe(row.afterExact_sha256);
    expect(obsoleteDigests.has(digest(value))).toBe(false);
  });
  it("contains none of the three obsolete sentence literals in the rendering sources", () => {
    for (const source of [rotateSource, pageSource]) {
      for (const match of source.matchAll(/(["`])([^\n]*?)\1/g)) {
        expect(obsoleteDigests.has(digest(match[2]))).toBe(false);
      }
    }
  });
});
