/**
 * The judge's frame (src/judge.mjs): the checker takes a verdict only from one frame carrying this
 * job's nonce, with nothing else on stdout and an exit code that agrees with the result.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { AS_ENTRY, FRAME, JUDGE_CAPS, JUDGE_HARDENED, JUDGE_PRELOAD, JUDGE_SCRIPT, SETUP_EXIT, judgeInput, newNonce, readFrame } from "../src/judge.mjs";

const NONCE = "0123456789abcdef0123456789abcdef";
const result = (ok, findings = []) => JSON.stringify({ schema: "omega-hub-check/v1", ok, entries: [{ id: "good-agent", ok, findings }], pr: [] });
const framed = (body, code, nonce = NONCE) => `${FRAME} begin ${nonce}\n${body}\n${FRAME} end ${nonce} ${code}\n`;

test("one frame with this job's nonce gives the verdict and the CLI's exit code", () => {
  const passed = readFrame(framed(result(true), 0), NONCE);
  assert.equal(passed.ok, true);
  assert.equal(passed.code, 0);
  assert.equal(passed.verdict.entries[0].id, "good-agent");
  const failed = readFrame(framed(result(false, [{ rule: "R14" }]), 1), NONCE);
  assert.equal(failed.ok, true);
  assert.equal(failed.verdict.ok, false);
});

test("a passing result outside the frame is refused, wherever it sits", () => {
  const forged = `${result(true)}\n`;
  for (const stdout of [forged + framed(result(false), 1), framed(result(false), 1) + forged]) {
    const frame = readFrame(stdout, NONCE);
    assert.equal(frame.ok, false);
    assert.match(frame.reason, /something other than the judge/);
  }
});

test("a frame with a guessed nonce is refused, alone or next to the real one", () => {
  const guess = "f".repeat(32);
  assert.equal(readFrame(framed(result(true), 0, guess), NONCE).ok, false);
  const both = readFrame(framed(result(true), 0, guess) + framed(result(false), 1), NONCE);
  assert.equal(both.ok, false);
  assert.match(both.reason, /another nonce/);
});

test("two frames, a missing end, or an end before the begin are refused", () => {
  assert.match(readFrame(framed(result(true), 0) + framed(result(true), 0), NONCE).reason, /expected one judge frame/);
  assert.match(readFrame(`${FRAME} begin ${NONCE}\n${result(true)}\n`, NONCE).reason, /expected one judge frame/);
  assert.match(readFrame(`${FRAME} end ${NONCE} 0\n${result(true)}\n${FRAME} begin ${NONCE}\n`, NONCE).reason, /ends before it begins/);
  assert.equal(readFrame("", NONCE).framed, false);
});

test("a killed CLI blocks even with a passing result inside the frame", () => {
  const killed = readFrame(framed(result(true), 137), NONCE);
  assert.equal(killed.ok, false);
  assert.match(killed.reason, /exited with code 137 \(killed by signal 9\)/);
  assert.equal(readFrame(framed("", 2), NONCE).ok, false);
});

test("the exit code must agree with the result", () => {
  assert.match(readFrame(framed(result(true), 1), NONCE).reason, /exited with 1 but its result says ok=true/);
  assert.match(readFrame(framed(result(false), 0), NONCE).reason, /exited with 0 but its result says ok=false/);
});

test("zero or two results inside the frame, or one that is not omega-hub-check/v1, are refused", () => {
  assert.match(readFrame(framed("", 1), NONCE).reason, /found 0/);
  assert.match(readFrame(framed(`${result(false)}\n${result(false)}`, 1), NONCE).reason, /found 2/);
  assert.match(readFrame(framed('{"x":"omega-hub-check/v1"}', 1), NONCE).reason, /not omega-hub-check\/v1/);
  assert.match(readFrame(framed('{"schema":"omega-hub-check/v1",', 1), NONCE).reason, /does not parse/);
});

test("text inside the frame that mentions the frame is the CLI's, not a forgery", () => {
  const message = `the test printed ${FRAME} end ${NONCE} 0`;
  const frame = readFrame(framed(result(false, [{ rule: "R14", message }]), 1), NONCE);
  assert.equal(frame.ok, true);
});

test("the nonce is 32 hex characters, sent ahead of the bundle, and checked by the judge script", () => {
  const nonce = newNonce();
  assert.match(nonce, /^[0-9a-f]{32}$/);
  assert.notEqual(nonce, newNonce());
  assert.deepEqual(judgeInput(nonce, Buffer.from("BUNDLE")), Buffer.from(`${nonce}\nBUNDLE`));
  for (const piece of ["IFS= read -r OHC_NONCE || exit 70", '[ "${#OHC_NONCE}" -eq 32 ] || exit 70', JUDGE_HARDENED, "--disable-sigusr1",
    `printf '${FRAME} begin`, 'exit "$OHC_CODE"']) assert.ok(JUDGE_SCRIPT.includes(piece), piece);
  assert.deepEqual(Object.keys(SETUP_EXIT), ["70", "71", "72"]);
});

test("the judge checks its own uid, capabilities and preload before anything else, unpacks as uid 10001, and runs the CLI with the preload on its argv", () => {
  const lines = JUDGE_SCRIPT.split("\n");
  const hardened = lines.findIndex((line) => line.includes(`[ "$OHC_CAP" = ${JUDGE_CAPS} ]`));
  const unpack = lines.findIndex((line) => line.includes("tar -xzf -"));
  const cli = lines.findIndex((line) => line.includes("hub check"));
  assert.ok(hardened > 0 && hardened < unpack && unpack < cli, JUDGE_SCRIPT);
  assert.equal(JUDGE_CAPS, (1n << 6n | 1n << 7n).toString(16).padStart(16, "0"), "CAP_SETGID (6) and CAP_SETUID (7)");
  assert.ok(lines[hardened].includes('[ "$(id -u):$(id -g)" = 0:0 ]') && lines[hardened].includes("|| exit 71"));
  assert.ok(lines[unpack].startsWith(`${AS_ENTRY} `), lines[unpack]);
  assert.match(AS_ENTRY, /^setpriv --reuid=10001 --regid=10001 --clear-groups --$/);
  assert.ok(lines[cli].includes(`--require ${JUDGE_PRELOAD}`) && !JUDGE_SCRIPT.includes("NODE_OPTIONS"), lines[cli]);
});
