import _ from "lodash";

// `json` and `json_object` are two spellings of the same option: the run-prompt
// backend advertises `json` while the develop UI stores `json_object`.
// `json_schema` is deliberately excluded — it is a distinct format.
const JSON_ALIASES = new Set(["json", "json_object"]);

const FORMAT_LABELS = {
  text: "Text",
  json: "JSON",
  json_object: "JSON",
  none: "None",
};

export function canonicalResponseFormat(value) {
  if (typeof value !== "string") return value;
  const lowered = value.toLowerCase();
  return JSON_ALIASES.has(lowered) ? "json_object" : value;
}

export function responseFormatLabel(value) {
  if (typeof value !== "string") return value;
  return FORMAT_LABELS[value.toLowerCase()] ?? _.startCase(value);
}

export function buildResponseFormatMenu({
  defaults = [],
  responseSchema = [],
  modelResponseFormat = [],
} = {}) {
  const menus = [];
  const seen = new Set();

  const add = (item) => {
    const key = canonicalResponseFormat(item.value);
    if (seen.has(key)) return;
    seen.add(key);
    menus.push(item);
  };

  defaults.forEach(add);
  responseSchema?.forEach((item) => add({ label: item.name, value: item.id }));
  modelResponseFormat?.forEach((item) =>
    add({ label: responseFormatLabel(item.value), value: item.value }),
  );

  return menus;
}
