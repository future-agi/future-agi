import { slugify } from "../../helpers/slugify";

// A world actor's id rule; the server applies it to every actor.
const ID_SOURCE = "actors.json";

export const lines = (value) =>
  (value || "")
    .split("\n")
    .map((one) => one.trim())
    .filter(Boolean);

export const words = (key) => key.replace(/_/g, " ");

// A view value as a person reads it: lists joined, objects as "field: value" pairs, never raw JSON. A typed entry
// reads as its content (a fact's text), and a `kind` tag as its value alone.
export const readable = (value) => {
  if (value == null) return "";
  if (Array.isArray(value))
    return value.map(readable).filter(Boolean).join("; ");
  if (typeof value === "object") {
    if (typeof value.text === "string") return value.text;
    return Object.entries(value)
      .map(([key, one]) => [key, readable(one)])
      .filter(([, one]) => one)
      .map(([key, one]) => (key === "kind" ? one : `${words(key)}: ${one}`))
      .join(" · ");
  }
  return String(value);
};

// `never_sees` names: grader material (`scenario.*`, `file.*`) and other actors' private fields (`<id>.<field>`).
export const hidden = (names) => {
  const grader = [];
  const others = {};
  for (const name of names || []) {
    const [head, ...rest] = name.split(".");
    const field = rest.join(".");
    // A file keeps its own name; a field reads as words.
    if (head === "file") grader.push(field);
    else if (head === "scenario") grader.push(words(field));
    else (others[head] ||= []).push(words(field));
  }
  // Every other actor keeps the same fields to itself, so one line per field set, not one per actor.
  const groups = {};
  for (const fields of Object.values(others)) {
    const key = fields.join(", ");
    groups[key] = (groups[key] || 0) + 1;
  }
  return { grader, others: Object.entries(groups) };
};

// An id made from a name, unique in the cast (`sam_lee`, `sam_lee_2`).
export const idFrom = (name, taken) => {
  const base = slugify(name, { separator: "_", fallback: "actor" }).slice(
    0,
    60,
  );
  let id = base;
  for (let n = 2; taken.has(id); n += 1) id = `${base}_${n}`;
  return id;
};

// The id rule from the record schemas the cast is sent with; none means the server alone checks.
export const idRule = (records) => {
  const pattern = records?.[ID_SOURCE]?.properties?.id?.pattern;
  return pattern ? new RegExp(pattern) : null;
};

// A sub-actor speaks on a call channel; a world actor has none.
export const speaks = (actor) => Boolean(actor?.channel);

// The tab shows what it can edit (versioning keeps a draft) or list (sub-actors read-only).
export const showActorsTab = (env) =>
  Boolean(env?.versioning?.enabled && env?.id) || env?.subActors?.length > 0;
