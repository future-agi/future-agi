import { genderLabel } from "../scenarios/personaLabels";

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

// The "Comes in" choice for a stored trigger the form cannot make (the cast says so per actor): kept as it is.
export const KEPT = "kept";

const CONSTRAINTS = ["may_agree", "private", "escalate_when", "stop_when"];
// The persona fields an actor's voice is picked from.
const VOICE_FIELDS = ["gender", "age_group", "accent"];
const TRIGGER_FIELDS = [
  "mode",
  "triggerTool",
  "triggerWords",
  "triggerTurn",
  "triggerSeconds",
  "triggerCollection",
  "triggerWhere",
];

// A nullable value as an input holds it.
const typed = (value) => (value == null ? "" : value);

// Empty is no value of its own; the server says when a field needs one.
const orNull = (text) => (String(text).trim() === "" ? null : text);
const numberOrNull = (text) =>
  String(text).trim() === "" ? null : Number(text);

// The trigger the form's fields make. Only read once `whereProblem` has nothing to say, so the text is a JSON object.
// A number goes as typed, and an emptied one as null: the server says when one is out of range or missing.
export const triggerOf = (read) => {
  switch (read("mode")) {
    case "on_tool":
      return { kind: "on_tool", tool: read("triggerTool") };
    case "on_keyword":
      return {
        kind: "on_keyword",
        words: String(read("triggerWords"))
          .split(",")
          .map((one) => one.trim())
          .filter(Boolean),
      };
    case "mid_journey":
      return { kind: "mid_journey", turn: numberOrNull(read("triggerTurn")) };
    case "after_seconds":
      return {
        kind: "after_seconds",
        seconds: numberOrNull(read("triggerSeconds")),
      };
    case "conditional": {
      // Blank or spaces is no match at all.
      const where = String(read("triggerWhere")).trim();
      return {
        kind: "conditional",
        collection: read("triggerCollection"),
        where: where ? JSON.parse(where) : {},
      };
    }
    default:
      return { kind: read("mode") };
  }
};

// Why the form's conditional "where" is not yet JSON the request can carry, or "".
export const whereProblem = (text) => {
  if (!String(text || "").trim()) return "";
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? ""
      : "an object of field values";
  } catch {
    return 'an object of field values, such as {"status": "pending"}';
  }
};

// A fallback action's arguments as typed: a JSON object, `{}` when empty; null when the text is not one.
export const parsedArguments = (text) => {
  if (!String(text ?? "").trim()) return {};
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : null;
  } catch {
    return null;
  }
};

// The fallback as the form's choice holds it: its kind and value together, so a picked action and a picked actor are
// told apart by the list they were picked from, never by guessing. `silent:silent`, `action:<id>`, `actor:<id>`.
export const fallbackKey = ({ kind, value }) => `${kind}:${value}`;
const fallbackValue = (key) => key.slice(key.indexOf(":") + 1);
export const fallbackActs = (key) => key.startsWith("action:");

// What a new actor's form starts from: nothing is sent for a field left as it is, so the service's defaults apply.
const NEW_FIELDS = {
  id: "",
  name: "",
  role: "",
  channel: "",
  loudness_ratio: null,
  gender: "",
  age_group: "",
  accent: "",
  kind: "person",
  parent: "",
  goal: "",
  may_agree: "",
  private: "",
  escalate_when: "",
  stop_when: "",
  facts: "",
  files: [],
  records: [],
  style: "",
  timing: "immediate",
  seconds: 0,
  permitted: [],
  play: "mocked",
  mode: "always",
  triggerTool: "",
  triggerWords: "",
  triggerTurn: 1,
  triggerSeconds: 0,
  triggerCollection: "",
  triggerWhere: "",
  timeout: 0,
  fallback: "silent:silent",
  fallback_arguments: "",
  model: "",
  max_turns: "",
  max_lines: "",
};

// What each form field starts from, read from the cast's typed actor.
export const initialFields = (actor) => {
  // A new actor starts on its side's default way of joining.
  if (!actor?.id) return { ...NEW_FIELDS, channel: typed(actor?.channel) };
  const { activation, behaviour, constraints } = actor;
  const { trigger } = activation;
  return {
    id: "",
    name: actor.name,
    role: actor.role,
    channel: typed(actor.channel),
    loudness_ratio: actor.loudness_ratio,
    ...Object.fromEntries(
      VOICE_FIELDS.map((one) => [one, typed(actor.persona?.[one])]),
    ),
    kind: actor.kind,
    parent: typed(actor.parent),
    goal: actor.goal,
    ...Object.fromEntries(
      CONSTRAINTS.map((one) => [one, constraints[one].join("\n")]),
    ),
    facts: actor.facts.join("\n"),
    files: actor.files,
    records: actor.records,
    style: behaviour.style,
    timing: behaviour.response_timing.kind,
    seconds: behaviour.response_timing.seconds,
    permitted: actor.permitted_actions,
    play: actor.play,
    mode: actor.editable_trigger ? trigger.kind : KEPT,
    // A trigger keeps only its own kind's keys; another kind's field starts where a new actor's does.
    triggerTool: "tool" in trigger ? trigger.tool : NEW_FIELDS.triggerTool,
    triggerWords:
      "words" in trigger ? trigger.words.join(", ") : NEW_FIELDS.triggerWords,
    triggerTurn: "turn" in trigger ? trigger.turn : NEW_FIELDS.triggerTurn,
    triggerSeconds:
      "seconds" in trigger ? trigger.seconds : NEW_FIELDS.triggerSeconds,
    triggerCollection:
      "collection" in trigger
        ? trigger.collection
        : NEW_FIELDS.triggerCollection,
    triggerWhere:
      trigger.where && Object.keys(trigger.where).length
        ? JSON.stringify(trigger.where)
        : "",
    timeout: activation.timeout_seconds,
    fallback: fallbackKey(actor.fallback),
    fallback_arguments:
      activation.fallback_arguments &&
      Object.keys(activation.fallback_arguments).length
        ? JSON.stringify(activation.fallback_arguments)
        : "",
    model: typed(actor.model),
    max_turns: typed(actor.max_turns),
    max_lines: typed(actor.max_lines),
  };
};

const differs = (one, other) =>
  Array.isArray(one) || Array.isArray(other)
    ? JSON.stringify(one) !== JSON.stringify(other)
    : String(one ?? "").trim() !== String(other ?? "").trim();

// Whether anything typed differs from where it started; typing something and deleting it again is no change.
export const hasEdits = (actor, fields) => {
  const start = initialFields(actor);
  return Object.keys(fields).some((name) => differs(fields[name], start[name]));
};

/**
 * The typed patch a save sends: only the fields the person really edited, by the request's own names. The service
 * merges it into the stored record and keeps everything it does not name. A new actor also carries the side it was
 * added on.
 * @param {Object} actor The cast's actor the form is editing; `{side}` for a new one.
 * @param {Object} fields What the form holds, by field name, for the fields touched.
 */
export const changesOf = (actor, fields) => {
  const start = initialFields(actor);
  const edited = (name) => name in fields && differs(fields[name], start[name]);
  const read = (name) => (name in fields ? fields[name] : start[name]);
  // A new actor carries its side, and the way of joining it started on unless that was changed below.
  const changes = actor?.id
    ? {}
    : {
        side: actor.side,
        ...(start.channel ? { channel: start.channel } : {}),
      };
  if (edited("name")) changes.name = read("name").trim();
  if (edited("role")) changes.role = read("role").trim();
  if (edited("channel")) changes.channel = orNull(read("channel"));
  for (const name of VOICE_FIELDS) {
    if (edited(name)) changes[name] = read(name);
  }
  if (edited("loudness_ratio")) changes.loudness_ratio = read("loudness_ratio");
  if (edited("kind")) changes.kind = read("kind");
  if (edited("parent")) changes.parent = orNull(read("parent"));
  if (edited("goal")) changes.goal = read("goal");
  for (const name of CONSTRAINTS) {
    if (edited(name)) changes[name] = lines(read(name));
  }
  if (edited("facts")) changes.facts = lines(read("facts"));
  if (edited("files")) changes.files = read("files");
  if (edited("records"))
    changes.records = read("records").map(({ store, collection }) => ({
      store,
      collection,
    }));
  if (edited("style")) changes.style = read("style");
  if (edited("timing") || edited("seconds"))
    changes.response_timing = {
      kind: read("timing"),
      seconds: read("timing") === "delayed" ? numberOrNull(read("seconds")) : 0,
    };
  if (edited("permitted")) changes.permitted_actions = read("permitted");
  if (edited("play")) changes.play = read("play");
  if (TRIGGER_FIELDS.some(edited) && read("mode") !== KEPT)
    changes.trigger = triggerOf(read);
  if (edited("timeout"))
    changes.timeout_seconds = numberOrNull(read("timeout"));
  if (edited("fallback")) changes.fallback = fallbackValue(read("fallback"));
  if (edited("fallback_arguments")) {
    const given = parsedArguments(read("fallback_arguments"));
    changes.fallback_arguments =
      given && Object.keys(given).length ? given : null;
  }
  if (edited("model")) changes.model = orNull(read("model"));
  for (const name of ["max_turns", "max_lines"]) {
    if (edited(name)) changes[name] = numberOrNull(read(name));
  }
  return changes;
};

// Which form field a server problem is about, by the request field it names; one not listed is shown above Save.
export const PROBLEM_FIELDS = {
  id: "id",
  name: "name",
  role: "role",
  channel: "channel",
  kind: "kind",
  facts: "facts",
  files: "files",
  records: "records",
  response_timing: "seconds",
  permitted_actions: "permitted",
  play: "play",
  trigger: "mode",
  timeout_seconds: "timeout",
  fallback: "fallback",
  fallback_arguments: "fallback_arguments",
  model: "model",
  max_turns: "max_turns",
  max_lines: "max_lines",
};
