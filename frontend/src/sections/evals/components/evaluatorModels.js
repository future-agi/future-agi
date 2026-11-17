// The evaluator models the model selector (ModelSelector) offers at the top
// of its list, with the names it shows them by: Future AGI's Turing models and
// Jev. The one place those names are kept; other screens name a model by them.

import { NATIVE_JEV_LABELS } from "../utils/decisionModels";

// ---------------------------------------------------------------------------
// FAGI built-in models — shown in a featured section at the top
// ---------------------------------------------------------------------------
export const FAGI_MODELS = [
  {
    value: "turing_large",
    label: "Turing Large",
    description: "Best accuracy for complex evaluations",
    icon: "solar:star-bold",
  },
  {
    value: "turing_small",
    label: "Turing Small",
    description: "Balanced accuracy, lower cost",
    icon: "solar:star-2-linear",
  },
  {
    value: "turing_flash",
    label: "Turing Flash",
    description: "Fast, low-latency evaluations",
    icon: "solar:bolt-linear",
  },
];

export const FAGI_MODEL_VALUES = new Set(FAGI_MODELS.map((m) => m.value));

// Jev's native models, named as the decision catalog names them
// (NATIVE_JEV_LABELS), so there is one list of them.
export const JEV_MODELS = Object.entries(NATIVE_JEV_LABELS).map(
  ([value, label]) => ({ value, label }),
);
export const JEV_MODEL_VALUES = new Set(JEV_MODELS.map((m) => m.value));

const LABELS = new Map(
  [...FAGI_MODELS, ...JEV_MODELS].map((model) => [model.value, model.label]),
);

/**
 * The name the model selector shows `value` by, or null when it has none: a
 * Jev or FAGI model's label. A stray bare value ("small", "large", "flash"),
 * which the backend's eval-list endpoint sometimes returns for built-in
 * templates, resolves to its FAGI label instead of showing as "small".
 */
export function evaluatorModelLabel(value) {
  if (!value) return null;
  const listed = LABELS.get(value);
  if (listed) return listed;
  const bare = FAGI_MODELS.find((m) => m.value.endsWith(`_${value}`));
  return bare?.label ?? null;
}
