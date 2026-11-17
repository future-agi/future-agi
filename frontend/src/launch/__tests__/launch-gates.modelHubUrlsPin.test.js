// The launch gates pin model_hub/urls.py by its sha256 so a route change is
// re-reviewed in the same commit. This checks that one pin on its own: the
// gates test stops at the first pin that does not match, so a stale pin
// further down the list would hide this one.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";

const read = (path) => readFileSync(resolve(process.cwd(), path), "utf8");
const sha256 = (text) => createHash("sha256").update(text).digest("hex");

describe("the launch gates' model_hub/urls.py pin", () => {
  it("matches the file it pins", () => {
    const gates = read("src/launch/__tests__/launch-gates.test.jsx");
    const pin = gates.match(
      /"futureagi\/model_hub\/urls\.py":\s*"([0-9a-f]{64})"/,
    )?.[1];
    expect(pin).toBeTruthy();
    expect(sha256(read("../futureagi/model_hub/urls.py"))).toBe(pin);
  });
});
