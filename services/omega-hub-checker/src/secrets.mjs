/**
 * Step 4, secrets (SPEC §4.2 row 4): R6's patterns plus the checker's own. Every match is redacted
 * in every report: the finding names the kind of secret and its first four characters only.
 */
import { SECRET_PATTERNS } from "../vendor/omega-hub/src/index.mjs";
import { block } from "./findings.mjs";

const EXTRA = [
  ["a private key", /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ["a JSON web token", /\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/],
  ["a connection string with a password", /\b[a-z][a-z0-9+.-]{1,20}:\/\/[^\s/:@"'`]+:[^\s/@"'`]{3,}@[^\s"'`]+/i],
  ["a Hugging Face token", /\bhf_[A-Za-z0-9]{30,}\b/],
  ["a Stripe live key", /\b[sr]k_live_[A-Za-z0-9]{16,}\b/],
  ["a SendGrid key", /\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b/],
  ["a Twilio key", /\bSK[0-9a-fA-F]{32}\b/],
];
const ASSIGNMENT = /\b([A-Za-z0-9_]*(?:_KEY|_TOKEN|_SECRET))\b\s*[:=]\s*["'`]([^"'`\s]{20,})["'`]/g;

export function entropy(value) {
  const counts = new Map();
  for (const char of value) counts.set(char, (counts.get(char) ?? 0) + 1);
  let bits = 0;
  for (const count of counts.values()) {
    const p = count / value.length;
    bits -= p * Math.log2(p);
  }
  return bits;
}

const redact = (value) => `${value.slice(0, 4)}… (redacted)`;
const lineOf = (text, index) => text.slice(0, index).split("\n").length;
const PLACEHOLDER = /^(x+|\.+|<[^>]*>|your[-_ ].*|example.*|changeme|replace[-_ ]?me|\*+)$/i;
// Lower-case words joined by hyphens, underscores or spaces ("local-endpoint-needs-no-key") read as
// prose, not as a token: real credentials carry digits or upper case. Such a literal is not flagged
// by the entropy rule (the named patterns above still apply to it).
const WORDS = /^[a-z]+(?:[-_ ][a-z]+)+$/;

export function secretFindings(files) {
  const out = [];
  for (const [rel, data] of files) {
    const text = data.toString("utf8");
    if (text.includes("\u0000")) continue;
    for (const [label, pattern] of [...SECRET_PATTERNS, ...EXTRA]) {
      if (label === "a personal home path") continue; // R6 already reports it in the manifest step
      const match = pattern.exec(text);
      if (match) out.push(block("secrets", "secret", rel, `holds what looks like ${label}: ${redact(match[0])}`, { line: lineOf(text, match.index), fix: "remove it; entries name keys, never values" }));
    }
    for (const match of text.matchAll(ASSIGNMENT)) {
      const value = match[2];
      if (PLACEHOLDER.test(value) || WORDS.test(value) || entropy(value) < 3.5) continue;
      out.push(block("secrets", "secret", rel, `${match[1]} is set to a literal that looks like a credential: ${redact(value)}`, { line: lineOf(text, match.index), fix: "read it from the environment, named in keys[]" }));
    }
  }
  return out;
}
