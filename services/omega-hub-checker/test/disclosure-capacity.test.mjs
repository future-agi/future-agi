import assert from "node:assert/strict";
import { test } from "node:test";
import { disclosureErrors } from "../src/registry-rules.mjs";

const value = {
  summary: "A complete disclosure confirmed in review.",
  network: "None.", files: "None.", shell: "None.", background: "None.", credentials: "None.",
};
for (const field of ["network", "files", "credentials"]) {
  test(`${field} accepts 300, 301, 900 and 1200 but rejects 1201 characters`, () => {
    for (const length of [4, 300, 301, 900, 1200]) {
      assert.deepEqual(disclosureErrors({ ...value, [field]: "x".repeat(length) }), []);
    }
    for (const length of [3, 1201]) assert.notEqual(disclosureErrors({ ...value, [field]: "x".repeat(length) }).length, 0);
  });
}
test("summary, shell, background and unknown fields retain their bounds", () => {
  for (const field of ["summary", "shell", "background"]) {
    const max = field === "summary" ? 200 : 300;
    assert.deepEqual(disclosureErrors({ ...value, [field]: "x".repeat(max) }), []);
    assert.notEqual(disclosureErrors({ ...value, [field]: "x".repeat(max + 1) }).length, 0);
  }
  assert.notEqual(disclosureErrors({ ...value, details: {} }).length, 0);
});
