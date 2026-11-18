export const lines = (value) =>
  (value || "")
    .split("\n")
    .map((one) => one.trim())
    .filter(Boolean);

export const words = (key) => key.replace(/_/g, " ");

// Keeps the starting value listed so a select never goes blank on it.
export const withStart = (options, start, label = start) =>
  start && !options.some((one) => one.value === start)
    ? [...options, { value: start, label }]
    : options;

// "Comes in" choice for a stored trigger the form cannot make; it is kept as is.
export const KEPT = "kept";

const CONSTRAINTS = ["may_agree", "private", "escalate_when", "stop_when"];
const VOICE_FIELDS = ["gender", "age_group", "accent"];
// The form field each trigger key the cast's `trigger_fields` names is typed in.
export const TRIGGER_INPUTS = {
  tool: "triggerTool",
  words: "triggerWords",
  turn: "triggerTurn",
  seconds: "triggerSeconds",
  collection: "triggerCollection",
  where: "triggerWhere",
};
const TRIGGER_FIELDS = ["mode", ...Object.values(TRIGGER_INPUTS)];

const typed = (value) => (value == null ? "" : value);

const orNull = (text) => (String(text).trim() === "" ? null : text);
const numberOrNull = (text) =>
  String(text).trim() === "" ? null : Number(text);

// How each trigger key's input becomes its value: words as a list, numbers as typed (emptied is null), a match as
// JSON (only read once `whereProblem` is "").
const TRIGGER_VALUES = {
  words: (text) =>
    String(text)
      .split(",")
      .map((one) => one.trim())
      .filter(Boolean),
  turn: numberOrNull,
  seconds: numberOrNull,
  where: (text) => (String(text).trim() ? JSON.parse(text) : {}),
};

// The trigger the form's fields make: its kind and each key the cast's `trigger_fields` lists for that kind.
export const triggerOf = (read, keys) => ({
  kind: read("mode"),
  ...Object.fromEntries(
    keys.map((key) => {
      const typedIn = read(TRIGGER_INPUTS[key]);
      return [key, TRIGGER_VALUES[key] ? TRIGGER_VALUES[key](typedIn) : typedIn];
    }),
  ),
});

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

// `{}` when empty; null when the text is not a JSON object.
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

// `silent:silent`, `action:<id>` or `actor:<id>`, so an action and an actor with one id stay apart.
export const fallbackKey = ({ kind, value }) => `${kind}:${value}`;
const fallbackValue = (key) => key.slice(key.indexOf(":") + 1);
export const fallbackActs = (key) => key.startsWith("action:");

// A trigger key's value as its input holds it; a key the stored trigger lacks starts empty.
const triggerInput = (trigger, key) => {
  if (!(key in trigger)) return "";
  if (key === "words") return trigger.words.join(", ");
  if (key === "where")
    return Object.keys(trigger.where).length
      ? JSON.stringify(trigger.where)
      : "";
  return trigger[key];
};

// What each field starts from: the cast's actor, or for a new one the cast's `new_actor` on its side.
export const initialFields = (actor) => {
  if (!actor) return {};
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
    ...Object.fromEntries(
      Object.entries(TRIGGER_INPUTS).map(([key, input]) => [
        input,
        triggerInput(trigger, key),
      ]),
    ),
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

export const hasEdits = (actor, fields) => {
  const start = initialFields(actor);
  return Object.keys(fields).some((name) => differs(fields[name], start[name]));
};

/** Only the edited fields, by request name; the service merges them. `triggerKeys` maps a kind to its trigger keys. */
export const changesOf = (actor, fields, triggerKeys = {}) => {
  const start = initialFields(actor);
  const edited = (name) => name in fields && differs(fields[name], start[name]);
  const read = (name) => (name in fields ? fields[name] : start[name]);
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
      seconds:
        read("timing") === "delayed" ? numberOrNull(read("seconds")) : null,
    };
  if (edited("permitted")) changes.permitted_actions = read("permitted");
  if (edited("play")) changes.play = read("play");
  if (TRIGGER_FIELDS.some(edited) && read("mode") !== KEPT)
    changes.trigger = triggerOf(read, triggerKeys[read("mode")] || []);
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

// Request field to form field; a problem on an unlisted one shows above Save.
export const PROBLEM_FIELDS = {
  id: "id",
  name: "name",
  role: "role",
  channel: "channel",
  gender: "gender",
  age_group: "age_group",
  accent: "accent",
  loudness_ratio: "loudness_ratio",
  kind: "kind",
  parent: "parent",
  goal: "goal",
  may_agree: "may_agree",
  private: "private",
  escalate_when: "escalate_when",
  stop_when: "stop_when",
  style: "style",
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

const MORE_DETAILS = [...CONSTRAINTS, "parent", "goal", "style"];

// A server problem on a field not in this set shows above Save.
export const fieldsOnScreen = (read, { capField, loud, models, more }) =>
  new Set([
    ...[
      "id",
      "name",
      "role",
      "channel",
      "kind",
      "facts",
      "files",
      "records",
      "permitted",
      "play",
      "mode",
      "timeout",
      "fallback",
      "timing",
    ],
    ...(capField ? [capField] : []),
    ...(read("channel") ? VOICE_FIELDS : []),
    ...(read("channel") && loud ? ["loudness_ratio"] : []),
    ...(read("timing") === "delayed" ? ["seconds"] : []),
    ...(fallbackActs(read("fallback")) ? ["fallback_arguments"] : []),
    ...(models ? ["model"] : []),
    ...(more ? MORE_DETAILS : []),
  ]);
