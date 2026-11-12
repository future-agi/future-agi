// What a reviewer sees first on an experiment-review item (R3 analysis
// fix): the task in one line, then the replies side by side; the full
// input, the expected checks and the metadata sit in a collapsed
// `Task details`. Pure helpers, no React.

import { parseJsonString } from "src/sections/common/ValueViewer/valueKind";

const isBlank = (v) =>
  v == null ||
  (typeof v === "string" && ["", "[]", "{}"].includes(v.trim())) ||
  (Array.isArray(v) && v.length === 0);

// Fields that usually hold the customer's ask, best first.
const TASK_KEYS = [
  "instruction",
  "customer_message",
  "message",
  "question",
  "query",
  "prompt",
  "input",
  "user_input",
];

const asValue = (v) => parseJsonString(v) ?? v;

/** Last user turn of a `[{role, content}]` transcript, else null. */
function lastUserTurn(v) {
  if (!Array.isArray(v)) return null;
  const turns = v.filter(
    (t) => t && typeof t === "object" && typeof t.content === "string",
  );
  const user = [...turns].reverse().find((t) => t.role === "user");
  return (user ?? turns[turns.length - 1])?.content ?? null;
}

/**
 * The task in one line: `{name, text}` from the input field that holds the
 * ask (`instruction`, `customer_message`, …), else the first plain-text
 * field, else the last user turn of a chat. Null when nothing is readable.
 * @param {object} input  `{fieldName: value}`
 */
export function taskOneLiner(input) {
  const entries = Object.entries(input ?? {}).filter(([, v]) => !isBlank(v));
  if (!entries.length) return null;
  const text = (v) => {
    const val = asValue(v);
    if (typeof val === "string") return val.trim();
    const turn = lastUserTurn(val);
    return turn ? turn.trim() : null;
  };
  const byKey = new Map(entries.map(([k, v]) => [k.toLowerCase(), [k, v]]));
  for (const key of TASK_KEYS) {
    const hit = byKey.get(key);
    const t = hit && text(hit[1]);
    if (t) return { name: hit[0], text: t };
  }
  for (const [k, v] of entries) {
    const t = text(v);
    if (t) return { name: k, text: t };
  }
  return null;
}

const isCheck = (c) =>
  c &&
  typeof c === "object" &&
  !Array.isArray(c) &&
  (typeof c.name === "string" || typeof c.key === "string") &&
  (typeof c.description === "string" || typeof c.type === "string");

/**
 * Expected checks as a plain list: when an expected field is a list of
 * check objects (`{key, name, type, description, draft}`), returns
 * `{field, items: [{key, name, description, type, draft}]}`; else null.
 * @param {object} expected  `{fieldName: value}`
 */
export function checklistOf(expected) {
  for (const [field, raw] of Object.entries(expected ?? {})) {
    const v = asValue(raw);
    if (Array.isArray(v) && v.length && v.every(isCheck)) {
      return {
        field,
        items: v.map((c, i) => ({
          key: c.key ?? `${field}-${i}`,
          name: c.name ?? c.key,
          description: c.description ?? "",
          type: c.type ?? null,
          draft: Boolean(c.draft),
        })),
      };
    }
  }
  return null;
}

const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/**
 * `6 input fields · 4 checks · 3 metadata` — what the collapsed
 * `Task details` holds, so a reviewer knows whether to open it.
 */
export function taskDetailsSummary({ input, expected, metadata } = {}) {
  const filled = (o) =>
    Object.values(o ?? {}).filter((v) => !isBlank(v)).length;
  const parts = [];
  const nIn = filled(input);
  if (nIn) parts.push(count(nIn, "input field", "input fields"));
  const checks = checklistOf(expected);
  if (checks) parts.push(count(checks.items.length, "check", "checks"));
  else {
    const nEx = filled(expected);
    if (nEx) parts.push(count(nEx, "expected field", "expected fields"));
  }
  const nMeta = filled(metadata);
  if (nMeta) parts.push(`${nMeta} metadata`);
  return parts.join(" · ");
}
