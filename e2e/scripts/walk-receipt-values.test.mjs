import assert from "node:assert/strict";
import test from "node:test";
import { assertValueFreeReceipt } from "../lib/walk/receipt-values.mjs";

const uuid = ["01234567", "89ab", "4cde", "8fab", "0123456789ab"].join("-");
const email = ["synthetic.actor", "example.invalid"].join("@");
const samples = [
  ["email", email],
  ["UUID", uuid],
  ["key", "sk-" + "syntheticOnly123".repeat(3)],
  ["key", "sk-test"],
  ["key", "fi_" + "synthetic".repeat(3)],
  ["key", "gsk_" + "synthetic".repeat(3)],
  ["key", "Bearer synthetic-only"],
  ["key", "Basic c3ludGhldGljOm9ubHk="],
  ["key", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.signature"],
  ["key", "-----BEGIN PRIVATE KEY-----"],
  ["key", "AKIA" + "A".repeat(16)],
  ["key", "AIza" + "a".repeat(35)],
  ["key", "ab".repeat(16)],
  ["key", "a".repeat(64)],
  ["key", "OpaqueSynthetic0123456789MaterialONLY"],
  ["key", "api_key=synthetic"],
];
for (const [i, [kind, sample]] of samples.entries()) {
  test(`receipt scanner refuses nested value and property name case ${i}`, () => {
    for (const value of [{ rows: [{ detail: sample }] }, { [sample]: 1 }]) {
      assert.throws(() => assertValueFreeReceipt(JSON.stringify(value)), {
        message: `walk receipt refused: ${kind}-shaped value`,
      });
    }
  });
}
test("receipt scanner sees serialized toJSON and JSON-escaped identities", () => {
  assert.throws(
    () =>
      assertValueFreeReceipt(JSON.stringify({ data: { toJSON: () => email } })),
    /email-shaped/,
  );
  assert.throws(
    () =>
      assertValueFreeReceipt(
        JSON.stringify({ value: email }).replace("@", "\\u0040"),
      ),
    /email-shaped/,
  );
});
test("receipt scanner rejects named credentials even when short or structured", () => {
  for (const name of [
    "apiKey",
    "api_key",
    "accessToken",
    "refresh_token",
    "client_secret",
    "authorization",
    "password",
  ]) {
    for (const value of ["short", ["short"], { value: "short" }]) {
      assert.throws(
        () =>
          assertValueFreeReceipt(JSON.stringify({ nested: { [name]: value } })),
        /key-shaped/,
      );
    }
  }
});
test("receipt scanner accepts public provenance and counts, not hashes in arbitrary fields", () => {
  const head = "a".repeat(40);
  assert.doesNotThrow(() =>
    assertValueFreeReceipt(
      JSON.stringify({
        realHead: head,
        productHead: head,
        images: {
          head,
          images: [
            {
              service: "fi-collector",
              tag: "futureagi/fi-collector:walk-public",
              imageId: `sha256:${"b".repeat(64)}`,
            },
          ],
        },
        mountedConfig: `sha256:${"c".repeat(64)}`,
        orgs: ["A", "B"],
        roles: [{ role: "owner", level: 10 }],
        verdicts: [{ where: "GET /-/orgs/{A}/config", verdict: "OFF" }],
        on: 0,
      }),
    ),
  );
  assert.throws(
    () => assertValueFreeReceipt(JSON.stringify({ extra: head })),
    /key-shaped/,
  );
  assert.throws(
    () => assertValueFreeReceipt(JSON.stringify({ realHead: email })),
    /email-shaped/,
  );
  assert.throws(() => assertValueFreeReceipt("invalid JSON"), /invalid JSON/);
});
