// Try it's sampling parameters. Empty means the model's own default, and
// nothing is sent. A parameter the catalog says the model does not take is
// offered disabled, with the reason, rather than sent and ignored.

const MAX_TOKENS_CEILING = 1_000_000;

export const PARAM_FIELDS = [
  {
    name: "temperature",
    label: "Temperature",
    min: 0,
    max: () => 2,
    step: 0.1,
    takes: ["temperature"],
  },
  {
    name: "max_tokens",
    label: "Max tokens",
    min: 1,
    // The model's own output limit, when the catalog knows it.
    max: (row) => row?.maxOutput || MAX_TOKENS_CEILING,
    step: 1,
    integer: true,
    takes: ["max_tokens", "max_completion_tokens"],
  },
  {
    name: "top_p",
    label: "Top P",
    min: 0,
    max: () => 1,
    step: 0.05,
    takes: ["top_p"],
  },
];

/** Whether the model takes a parameter; a model with no list takes any. */
export function takes(row, field) {
  const listed = row?.model?.supported_parameters;
  if (!Array.isArray(listed) || !listed.length) return true;
  return field.takes.some((p) => listed.includes(p));
}

const rangeText = (min, max) => `${min} to ${max.toLocaleString("en-US")}`;

/** { name: "0 to 2" } for each value set out of its range. */
export function paramErrors(values, row) {
  const errors = {};
  PARAM_FIELDS.forEach((field) => {
    const raw = values?.[field.name];
    if (raw === "" || raw == null || !takes(row, field)) return;
    const value = Number(raw);
    const max = field.max(row);
    const bad =
      !Number.isFinite(value) ||
      value < field.min ||
      value > max ||
      (field.integer && !Number.isInteger(value));
    if (bad) errors[field.name] = rangeText(field.min, max);
  });
  return errors;
}
