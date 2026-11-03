// Highlight for chosen values; primary.lighter washes out in dark mode.
export const accentSx = {
  bgcolor: (t) =>
    t.palette.mode === "dark"
      ? "rgba(120,87,252,0.26)"
      : t.palette.primary.lighter,
  color: (t) =>
    t.palette.mode === "dark" ? "#C7B9FF" : t.palette.primary.darker,
};

export const ENTRY = [
  ["min_findings", "Findings", "/hr"],
  ["entry_multiplier", "Times usual", "×"],
  ["entry_delta", "Above usual", "/hr"],
  ["entry_confirmation_minutes", "Hold for", "min"],
];
export const RECOVERY = [
  ["recovery_min", "Findings", "/hr"],
  ["recovery_multiplier", "Times usual", "×"],
  ["recovery_delta", "Above usual", "/hr"],
  ["recovery_confirmation_minutes", "Hold for", "min"],
];
export const FIELD_NAMES = [...ENTRY, ...RECOVERY].map(([name]) => name);

// Balanced is the server default; the other two are fixed and pass every
// backend range and cross-field check.
export const PRESETS = [
  {
    key: "sensitive",
    label: "Sensitive",
    hint: "Catches early",
    description: "Catch problems early. For low-traffic or critical agents.",
    values: {
      min_findings: 10,
      entry_multiplier: 2,
      entry_delta: 5,
      entry_confirmation_minutes: 10,
      recovery_min: 3,
      recovery_multiplier: 1.3,
      recovery_delta: 2,
      recovery_confirmation_minutes: 10,
    },
  },
  {
    key: "balanced",
    label: "Balanced",
    hint: "Most teams",
    description: "The default. Works for most agents.",
    values: null,
  },
  {
    key: "calm",
    label: "Calm",
    hint: "Big spikes only",
    description:
      "Only big, sustained spikes. For high-traffic or noisy agents.",
    values: {
      min_findings: 50,
      entry_multiplier: 4,
      entry_delta: 25,
      entry_confirmation_minutes: 30,
      recovery_min: 15,
      recovery_multiplier: 2,
      recovery_delta: 8,
      recovery_confirmation_minutes: 20,
    },
  },
];

export const presetValues = (preset, defaults) =>
  numericValues(preset.values ?? defaults);

export const numericValues = (values) =>
  Object.fromEntries(
    FIELD_NAMES.map((name) => [
      name,
      values[name] === "" || values[name] == null ? NaN : Number(values[name]),
    ]),
  );

// The API types both multipliers as decimal strings (see FeedEscalationSettingsBody).
export const toPayload = (values) => ({
  ...values,
  entry_multiplier: values.entry_multiplier.toFixed(1),
  recovery_multiplier: values.recovery_multiplier.toFixed(1),
});

export function matchPreset(values, defaults) {
  return (
    PRESETS.find((preset) => {
      const target = presetValues(preset, defaults);
      return FIELD_NAMES.every((name) => target[name] === values[name]);
    })?.key ?? null
  );
}

const fmt = (n) => n.toLocaleString("en-US");

export function validate(values, ranges) {
  const errors = {};
  for (const [name, range] of Object.entries(ranges)) {
    const value = values[name];
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < range.min ||
      value > range.max
    ) {
      errors[name] = `Enter ${fmt(range.min)} to ${fmt(range.max)}.`;
    } else if (
      Math.abs(value / range.step - Math.round(value / range.step)) > 1e-8
    ) {
      errors[name] = `Use steps of ${range.step}.`;
    }
  }
  for (const [recovery, entry, unit] of [
    ["recovery_min", "min_findings", ""],
    ["recovery_multiplier", "entry_multiplier", "×"],
    ["recovery_delta", "entry_delta", ""],
  ]) {
    if (
      !errors[recovery] &&
      !errors[entry] &&
      values[recovery] >= values[entry]
    )
      errors[recovery] = `Must be below ${fmt(values[entry])}${unit}.`;
  }
  return errors;
}

// Mirrors Policy.thresholds: escalate at or above the highest entry limit,
// recover at or below the highest recovery limit.
const limits = (values, usual, kind) =>
  kind === "entry"
    ? [
        ["min_findings", values.min_findings],
        ["entry_multiplier", values.entry_multiplier * usual],
        ["entry_delta", usual + values.entry_delta],
      ]
    : [
        ["recovery_min", values.recovery_min],
        ["recovery_multiplier", values.recovery_multiplier * usual],
        ["recovery_delta", usual + values.recovery_delta],
      ];

const highest = (pairs) =>
  pairs.reduce((top, pair) => (pair[1] > top[1] + 1e-9 ? pair : top));

export const entryLevel = (values, usual) =>
  highest(limits(values, usual, "entry"))[1];
export const recoveryLevel = (values, usual) =>
  highest(limits(values, usual, "recovery"))[1];
export const deciding = (values, usual) => [
  highest(limits(values, usual, "entry"))[0],
  highest(limits(values, usual, "recovery"))[0],
];
export const escalateAt = (values, usual) =>
  Math.ceil(entryLevel(values, usual) - 1e-9);
export const recoverAt = (values, usual) =>
  Math.floor(recoveryLevel(values, usual) + 1e-9);

const round1 = (n) => Math.round(n * 10) / 10;
export function explainLimit(values, usual, kind) {
  const entry = kind === "entry";
  const min = entry ? values.min_findings : values.recovery_min;
  const times = entry ? values.entry_multiplier : values.recovery_multiplier;
  const above = entry ? values.entry_delta : values.recovery_delta;
  return `Highest of ${fmt(min)} findings, ${times.toFixed(1)}× usual (${fmt(
    round1(times * usual),
  )}) and usual + ${fmt(above)} (${fmt(usual + above)}).`;
}

/* Sample issue for the preview chart: a flat baseline plus one burst, built at
   10/hr and scaled to the "usually gets N" value so the spike keeps its shape.
   Each point is the rolling-hour count that leaves out the latest 10 minutes,
   checked every minute against a steady usual so each hold starts exactly
   where the line crosses its limit. */
export const SAMPLE = { base: 10, span: 270, from: 20, to: 240 };
export const SAMPLE_COUNTS = (() => {
  const burstAt = 45;
  const extra = (t) => {
    const x = t - burstAt;
    return x > 0 ? 0.2 * x * Math.exp(-x / 19) : 0;
  };
  const cum = new Float64Array(SAMPLE.span + 1);
  for (let t = 1; t <= SAMPLE.span; t++) cum[t] = cum[t - 1] + extra(t - 0.5);
  const at = (t) => cum[Math.max(0, t)];
  const jitter = (t) =>
    0.3 * Math.sin(t / 7.3) +
    0.2 * Math.sin(t / 3.1 + 1) +
    0.15 * Math.sin(t / 17 + 2);
  return Array.from(
    { length: SAMPLE.span + 1 },
    (_, t) => SAMPLE.base + (at(t - 10) - at(t - 70)) + jitter(t),
  );
})();

export function simulate(values, usual) {
  const scale = usual / SAMPLE.base;
  const entry = entryLevel(values, usual);
  const recovery = recoveryLevel(values, usual);
  const spans = [];
  let escalating = false;
  let since = null;
  let current = null;
  for (let t = 0; t <= SAMPLE.span; t++) {
    const count = SAMPLE_COUNTS[t] * scale;
    const crossed = escalating ? count <= recovery : count >= entry;
    if (!crossed) {
      since = null;
      continue;
    }
    if (since === null) since = t;
    const hold = escalating
      ? values.recovery_confirmation_minutes
      : values.entry_confirmation_minutes;
    if (t - since < hold) continue;
    if (escalating) {
      current.recoverFrom = since;
      current.end = t;
    } else {
      current = { holdFrom: since, start: t, recoverFrom: null, end: null };
      spans.push(current);
    }
    escalating = !escalating;
    since = null;
  }
  return spans;
}
