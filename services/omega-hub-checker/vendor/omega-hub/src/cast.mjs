/**
 * @module @omega/hub/cast
 *
 * Recordings: asciicast v2 (one JSON header line, then `[seconds, "o", text]` events). This module
 * writes them from captured output, scrubs machine paths, applies `demo.redact`, checks them (R9)
 * and compares a recording with a fresh run (R15). Timings are never compared: only the text of the
 * output events after the same scrub and redact, CRLF as LF, trailing spaces trimmed.
 */

import { LIMITS, finding } from "./schema.mjs";

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Parse a recording. Returns `{ header, events }` or throws with the reason. */
export function parseCast(text) {
  const lines = String(text).split("\n").filter((line) => line.trim() !== "");
  if (lines.length === 0) throw new Error("the recording is empty");
  let header;
  try {
    header = JSON.parse(lines[0]);
  } catch {
    throw new Error("the first line is not a JSON header");
  }
  if (header?.version !== 2 || !Number.isInteger(header.width) || !Number.isInteger(header.height)) {
    throw new Error("the header is not asciicast v2 ({ version: 2, width, height })");
  }
  const events = lines.slice(1).map((line, index) => {
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      throw new Error(`event ${index + 1} is not JSON`);
    }
    if (!Array.isArray(event) || typeof event[0] !== "number" || typeof event[1] !== "string" || typeof event[2] !== "string") {
      throw new Error(`event ${index + 1} is not [seconds, type, text]`);
    }
    return event;
  });
  return { header, events };
}

/**
 * Replace machine-specific paths with stable tokens: each entry path with `.`, each temp dir with
 * `<tmp>`, the home dir with `~`. Longer paths first, so a temp dir inside home becomes `<tmp>`.
 * @param {string} text
 * @param {{ entryDirs?: string[]; tmpDirs?: string[]; home?: string }} [options]
 */
export function scrubText(text, { entryDirs = [], tmpDirs = [], home } = {}) {
  const pairs = [
    ...entryDirs.map((dir) => [dir, "."]),
    ...tmpDirs.map((dir) => [dir, "<tmp>"]),
    ...(home ? [[home, "~"]] : [])
  ]
    .filter(([from]) => typeof from === "string" && from.length > 1)
    .map(([from, to]) => [from.replace(/[\\/]+$/, ""), to])
    .sort((left, right) => right[0].length - left[0].length);
  let out = String(text);
  for (const [from, to] of pairs) out = out.replace(new RegExp(escapeRegExp(from), "g"), to);
  return out;
}

/** Apply `demo.redact`: each match becomes `<redacted>`, or `<label>` for a named group `(?<label>...)`. */
export function redactText(text, patterns = []) {
  let out = String(text);
  for (const source of patterns) {
    const pattern = new RegExp(source, "g");
    out = out.replace(pattern, (...args) => {
      const groups = args.at(-1);
      const label = groups && typeof groups === "object" ? Object.keys(groups)[0] : undefined;
      return label ? `<${label}>` : "<redacted>";
    });
  }
  return out;
}

/** The text a comparison reads: CRLF as LF, trailing spaces trimmed, trailing blank lines dropped. */
export function normalizeOutput(text) {
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n").map((line) => line.replace(/[ \t]+$/, ""));
  while (lines.length > 0 && lines.at(-1) === "") lines.pop();
  return lines.join("\n");
}

/** The concatenated output (`o`) events of a parsed recording. */
export function castOutput(parsed) {
  return parsed.events.filter((event) => event[1] === "o").map((event) => event[2]).join("");
}

/**
 * Write a recording from captured chunks `[{ at: seconds, text }]` (already scrubbed and redacted).
 * Newlines become CRLF, idle gaps are capped at one second, and the header has no timestamp so a
 * re-record of the same output is a clean diff.
 */
export function writeCast({ id, chunks }) {
  let clock = 0;
  let previous = 0;
  const events = [];
  for (const chunk of chunks) {
    if (!chunk.text) continue;
    const gap = Math.max(0, Math.min(LIMITS.idleSeconds, (chunk.at ?? previous) - previous));
    previous = chunk.at ?? previous;
    clock += gap;
    events.push([Number(clock.toFixed(3)), "o", chunk.text.replace(/\r?\n/g, "\r\n")]);
  }
  const lines = normalizeOutput(events.map((event) => event[2]).join("")).split("\n").length;
  const header = { version: 2, width: LIMITS.castWidth, height: Math.max(1, Math.min(LIMITS.castHeight, lines)), title: `${id} (keyless)`, env: { TERM: "xterm-256color" } };
  return `${[JSON.stringify(header), ...events.map((event) => JSON.stringify(event))].join("\n")}\n`;
}

/** @type {[string, RegExp][]} */
const MACHINE_PATHS = [
  ["a macOS home path", new RegExp("/" + "Users/")],
  ["a Linux home path", /\/home\//],
  ["a Windows home path", /[A-Za-z]:\\+Users/],
  ["an absolute temp path", /(?:^|[\s"'(=:])(?:\/tmp\/|\/var\/folders\/|\/private\/var\/|[A-Za-z]:\\+(?:Windows\\+)?Temp)/]
];

/** R9: the recording parses, fits the caps and holds no machine path. */
export function checkCast(text, file) {
  const out = [];
  const fail = (message, fix) => out.push(finding("R9", file, message, fix));
  if (Buffer.byteLength(text) > LIMITS.castBytes) fail(`the recording is ${Buffer.byteLength(text)} bytes; at most ${LIMITS.castBytes}`, "print less, or redact repeated output");
  let parsed;
  try {
    parsed = parseCast(text);
  } catch (error) {
    fail(`not an asciicast v2 recording: ${error instanceof Error ? error.message : String(error)}`, "re-record it with omega hub check <id> --record");
    return out;
  }
  const output = normalizeOutput(castOutput(parsed));
  const lines = output === "" ? 0 : output.split("\n").length;
  if (lines > LIMITS.castLines) fail(`the recording has ${lines} lines; at most ${LIMITS.castLines}`, "print less");
  if (lines === 0) fail("the recording shows no output", "print what the entry does");
  for (const [label, pattern] of MACHINE_PATHS) {
    if (pattern.test(output)) fail(`the recording shows ${label}`, "print relative paths, or add a demo.redact pattern");
  }
  return out;
}

/**
 * R15: compare a recording with a fresh run's output (both already scrubbed and redacted). Returns
 * null when they match, else `{ line, expected, actual }` for the first differing line (1-based).
 */
export function compareOutputs(recorded, fresh) {
  const left = normalizeOutput(recorded).split("\n");
  const right = normalizeOutput(fresh).split("\n");
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    if (left[index] !== right[index]) return { line: index + 1, expected: left[index] ?? "<end of recording>", actual: right[index] ?? "<end of output>" };
  }
  return null;
}

/** The last `count` non-empty lines of a recording's output: what a card's mini terminal types. */
export function tailLines(parsed, count = 8) {
  return normalizeOutput(castOutput(parsed)).split("\n").filter((line) => line.trim() !== "").slice(-count);
}
