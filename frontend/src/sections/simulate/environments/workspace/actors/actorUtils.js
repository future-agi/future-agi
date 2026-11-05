import { slugify } from "../../helpers/slugify";
import { genderLabel } from "../scenarios/personaLabels";

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

// A persona's own label already says what it is, so its voice fields read as values, as the actor's row shows them.
export const seenValue = (input, value) => {
  if (input !== "actor.persona" || !value || typeof value !== "object")
    return readable(value);
  const { name, gender, age_group: age, accent, ...rest } = value;
  return [name, genderLabel(gender), age, accent, readable(rest)]
    .filter(Boolean)
    .join(" · ");
};

// `never_sees`: grader material, sent as scenario fields and files apart, and other actors' private fields
// (`<name>.<field>`).
export const hidden = (neverSees) => {
  const grader = new Set();
  // Grader material reads as words, a file without its extension, so `sub_goals.json` and `sub_goals` are one line.
  for (const name of neverSees?.scenario_fields || []) grader.add(words(name));
  for (const name of neverSees?.files || [])
    grader.add(name.replace(/\.[^.]+$/, "").replace(/[-_]/g, " "));
  const others = {};
  for (const name of neverSees?.other_actors || []) {
    const at = name.lastIndexOf(".");
    (others[name.slice(0, at)] ||= []).push(words(name.slice(at + 1)));
  }
  // Every other actor keeps the same fields to itself, so one line per field set, not one per actor.
  const groups = {};
  for (const fields of Object.values(others)) {
    const key = fields.join(", ");
    groups[key] = (groups[key] || 0) + 1;
  }
  return { grader: [...grader], others: Object.entries(groups) };
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

// The id rule in the schema's own words; none when the schema does not describe it.
export const idRuleText = (records) =>
  records?.[ID_SOURCE]?.properties?.id?.description || "";

// An actor with a channel speaks in the conversation; every actor has every other field.
export const speaks = (actor) => Boolean(actor?.channel);

const CONSTRAINTS = ["may_agree", "private", "escalate_when", "stop_when"];
// The persona fields an actor's voice is picked from.
const VOICE_FIELDS = ["gender", "age_group", "accent"];

// The "Comes in" choice for a trigger the select can't make itself (a delay, a chance, a later call): kept as it is.
export const KEPT_TRIGGER = "__kept__";

const triggerChoice = (trigger) => {
  const kind = trigger?.kind || "always";
  if (kind === "always") return "";
  if (kind === "on_tool" && trigger.tool && !(trigger.on_call > 1))
    return trigger.tool;
  return KEPT_TRIGGER;
};

// A fact the form can show: one with text. Any other entry is kept where it is, untouched.
const isWrittenFact = (one) =>
  one?.kind === "facts" && typeof one.text === "string" && one.text.trim();

// The files and record collections an actor's knowledge names, as the form picks them.
const readFiles = (knowledge) =>
  (knowledge || [])
    .filter((one) => one?.kind === "files")
    .flatMap((one) => one.paths || []);
export const recordKey = (one) => `${one.store || ""}.${one.collection || ""}`;
const readRecords = (knowledge) =>
  (knowledge || []).filter((one) => one?.kind === "records").map(recordKey);

// The knowledge with what the form picked for files and records in place of what it named before; facts and any
// other entry stay where they were. A record keeps the rest of its entry (a `where`) when it is still picked.
const withReadable = (knowledge, files, records, sources) => {
  const kept = knowledge.filter(
    (one) => one?.kind !== "files" && one?.kind !== "records",
  );
  const before = Object.fromEntries(
    knowledge
      .filter((one) => one?.kind === "records")
      .map((one) => [recordKey(one), one]),
  );
  const offered = Object.fromEntries(
    (sources?.records || []).map((one) => [recordKey(one), one]),
  );
  return [
    ...kept,
    ...(files.length ? [{ kind: "files", paths: files }] : []),
    ...records.map(
      (key) =>
        before[key] || {
          kind: "records",
          store: offered[key]?.store ?? key.split(".")[0],
          collection:
            offered[key]?.collection ?? key.split(".").slice(1).join("."),
        },
    ),
  ];
};

// What each form field starts from, read from the record.
export const initialFields = (actor) => {
  const activation = actor?.activation || {};
  const timing = actor?.behaviour?.response_timing || {};
  const constraints = actor?.constraints || {};
  const persona = actor?.persona || {};
  return {
    id: "",
    name: actor?.name || "",
    role: actor?.role || "",
    channel: actor?.channel || "",
    loudness_ratio: actor?.loudness_ratio ?? null,
    ...Object.fromEntries(VOICE_FIELDS.map((one) => [one, persona[one] || ""])),
    kind: actor?.kind || "person",
    parent: actor?.parent || "",
    goal: actor?.goal || "",
    ...Object.fromEntries(
      CONSTRAINTS.map((one) => [one, (constraints[one] || []).join("\n")]),
    ),
    facts: (actor?.knowledge || [])
      .filter(isWrittenFact)
      .map((one) => one.text)
      .join("\n"),
    files: readFiles(actor?.knowledge),
    recordsRead: readRecords(actor?.knowledge),
    style: actor?.behaviour?.style || "",
    timing: timing.kind || "immediate",
    seconds: timing.seconds ?? 0,
    permitted: actor?.permitted_actions || [],
    play: actor?.play || "mocked",
    tool: triggerChoice(activation.trigger),
    timeout: activation.timeout_seconds ?? 0,
    fallback: activation.fallback || "silent",
  };
};

const differs = (one, other) =>
  String(one ?? "").trim() !== String(other ?? "").trim();

// Whether anything typed differs from where it started; typing something and deleting it again is no change.
export const hasEdits = (actor, fields) => {
  const start = initialFields(actor);
  return Object.keys(fields).some((name) => differs(fields[name], start[name]));
};

// A typed id, else the one made from the name; a saved id never changes.
export const actorId = (actor, fields, context) =>
  actor?.id ||
  (fields.id || "").trim() ||
  idFrom(fields.name, new Set((context.others || []).map((one) => one.id)));

// The typed lines as fact entries, in the typed order. A fact whose text is still there stays the same entry, so it
// keeps its other keys; its text may span lines, so the longest are tried first.
const factsFrom = (facts, typed) => {
  const unused = [...facts].sort(
    (a, b) => lines(b.text).length - lines(a.text).length,
  );
  const entries = [];
  let at = 0;
  while (at < typed.length) {
    const start = at;
    const same = unused.find((one) =>
      lines(one.text).every((line, n) => typed[start + n] === line),
    );
    if (same) {
      unused.splice(unused.indexOf(same), 1);
      entries.push(same);
      at += lines(same.text).length;
    } else {
      entries.push({ kind: "facts", text: typed[at] });
      at += 1;
    }
  }
  return entries;
};

// The facts go in the places facts held, any extra after the rest, so other knowledge keeps its order.
const withFacts = (knowledge, typed) => {
  const entries = factsFrom(knowledge.filter(isWrittenFact), typed);
  const placed = knowledge.flatMap((one) => {
    if (!isWrittenFact(one)) return [one];
    return entries.length ? [entries.shift()] : [];
  });
  return [...placed, ...entries];
};

/**
 * The record a save sends. An existing actor is its own record with only the really edited fields laid over it, so
 * nothing the form merely displayed is rewritten; a new actor sends everything a create carries.
 * @param {Object} actor The record the form is editing, `{}`-like for a new one.
 * @param {Object} fields What the form holds, by field name.
 * @param {{ others?: Object[], loudness?: { channels?: string[] } }} context
 */
export const toRecord = (actor, fields, context) => {
  const start = initialFields(actor);
  const isNew = !actor.id;
  const edited = (name) => name in fields && differs(fields[name], start[name]);
  const read = (name) => (edited(name) ? fields[name] : start[name]);
  const writes = (...names) => isNew || names.some(edited);
  const record = { ...actor };
  if (isNew) record.id = actorId(actor, fields, context);
  if (writes("name")) record.name = read("name").trim();
  if (writes("role")) record.role = read("role").trim();
  // The server refuses a record whose set persona name differs from its name, and the form has no field for it.
  if (edited("name") && String(actor.persona?.name ?? "").trim())
    record.persona = { ...actor.persona, name: record.name };

  // Every actor has the same fields; a channel only adds how it speaks in the conversation.
  if (writes("channel")) {
    if (read("channel")) record.channel = read("channel");
    else delete record.channel;
  }
  if (
    writes(...VOICE_FIELDS) &&
    (record.persona || VOICE_FIELDS.some((name) => read(name)))
  ) {
    record.persona = { ...record.persona };
    for (const name of VOICE_FIELDS) {
      if (read(name)) record.persona[name] = read(name);
      else delete record.persona[name];
    }
  }
  if (
    writes("channel", "loudness_ratio") &&
    (read("channel") || actor.loudness_ratio != null)
  )
    record.loudness_ratio = (context.loudness?.channels || []).includes(
      read("channel"),
    )
      ? read("loudness_ratio")
      : null;

  if (writes("kind")) record.kind = read("kind");
  if (writes("parent")) record.parent = read("parent") || null;
  if (writes("goal")) record.goal = read("goal");
  if (writes(...CONSTRAINTS)) {
    record.constraints = { ...actor.constraints };
    for (const name of CONSTRAINTS) {
      if (writes(name)) record.constraints[name] = lines(read(name));
    }
  }
  if (writes("facts"))
    record.knowledge = withFacts(actor.knowledge || [], lines(read("facts")));
  if (edited("files") || edited("recordsRead"))
    record.knowledge = withReadable(
      record.knowledge || actor.knowledge || [],
      read("files"),
      read("recordsRead"),
      context.knowledgeSources,
    );
  if (writes("style", "timing", "seconds")) {
    record.behaviour = { ...actor.behaviour };
    if (writes("style")) record.behaviour.style = read("style");
    if (writes("timing", "seconds"))
      record.behaviour.response_timing =
        read("timing") === "delayed"
          ? { kind: "delayed", seconds: Number(read("seconds")) || 0 }
          : { kind: "immediate", seconds: 0 };
  }
  if (writes("permitted")) record.permitted_actions = read("permitted");
  if (edited("play")) record.play = read("play");
  if (writes("tool", "timeout", "fallback")) {
    record.activation = { ...actor.activation };
    if (writes("tool"))
      record.activation.trigger = read("tool")
        ? { kind: "on_tool", tool: read("tool") }
        : { kind: "always" };
    if (writes("timeout"))
      record.activation.timeout_seconds = Number(read("timeout")) || 0;
    if (writes("fallback")) record.activation.fallback = read("fallback");
  }
  return record;
};

// The tab shows what it can edit (versioning keeps a draft) or list (actors read-only).
export const showActorsTab = (env) =>
  Boolean(env?.versioning?.enabled && env?.id) || env?.callActors?.length > 0;
