// routing.auto: the org's tuning of the gateway's `model: "auto"`. The gateway
// treats an unknown or missing preference as balanced and a cap of 0 as none.

export const AUTO_PREFERENCES = [
  {
    value: "cost",
    label: "Cost",
    description:
      "Leans toward cheaper models. Complex tasks still move up from the cheapest.",
  },
  {
    value: "balanced",
    label: "Balanced",
    description:
      "Matches price tier to the task: simple prompts go economical, complex ones to frontier models.",
  },
  {
    value: "quality",
    label: "Quality",
    description:
      "Leans toward frontier models. Simple prompts still skip the most expensive.",
  },
];

export const DEFAULT_PREFERENCE = "balanced";

const CAP_FIELDS = [
  ["maxInput", "max_input_per_mtok"],
  ["maxOutput", "max_output_per_mtok"],
];

export function draftFromAuto(auto) {
  const a = auto && typeof auto === "object" ? auto : {};
  const pref = AUTO_PREFERENCES.some((p) => p.value === a.preference)
    ? a.preference
    : DEFAULT_PREFERENCE;
  const draft = {
    preference: pref,
    candidates: Array.isArray(a.candidates)
      ? a.candidates.filter((c) => typeof c === "string" && c)
      : [],
  };
  CAP_FIELDS.forEach(([field, key]) => {
    const n = Number(a[key]);
    draft[field] =
      a[key] != null && Number.isFinite(n) && n > 0 ? String(n) : "";
  });
  return draft;
}

// "" is no cap; anything else must be a positive dollar amount. Returns the
// error message, or "".
export function capError(text) {
  const trimmed = String(text ?? "").trim();
  if (!trimmed) return "";
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n <= 0) {
    return "Enter an amount above $0, or leave blank for no cap";
  }
  return "";
}

export function draftsEqual(a, b) {
  return (
    a.preference === b.preference &&
    a.maxInput.trim() === b.maxInput.trim() &&
    a.maxOutput.trim() === b.maxOutput.trim() &&
    a.candidates.length === b.candidates.length &&
    a.candidates.every((c, i) => c === b.candidates[i])
  );
}

// The update-config patch for routing.auto. A cleared field is sent as null,
// which deletes the saved key; a field that was never saved is left out, so
// the patch says only what changed.
export function autoPatch(draft, savedAuto) {
  const saved = savedAuto && typeof savedAuto === "object" ? savedAuto : {};
  const patch = { preference: draft.preference };
  const clear = (key) => {
    if (key in saved) patch[key] = null;
  };

  if (draft.candidates.length) patch.candidates = [...draft.candidates];
  else clear("candidates");

  CAP_FIELDS.forEach(([field, key]) => {
    const trimmed = draft[field].trim();
    if (trimmed) patch[key] = Number(trimmed);
    else clear(key);
  });
  return patch;
}

// The price the org itself set for a model auto may pick on its own
// provider, as the gateway resolves it (modeldb/orgdb): a Cost Tracking
// custom price (while cost tracking is on) wins; else a Model Database
// override's input/output price, each field falling back to the gateway's
// model database. Returns { source, input, output } in dollars per 1M
// tokens (null for a field the org left to the model database), or null
// when the org set no price. Keys match as the gateway matches them: the
// model id, then the id without its "<provider>/" prefix.
export function orgPriceOf(config, id) {
  const lookup = (map) => {
    if (!map || typeof map !== "object" || !id) return null;
    if (map[id]) return map[id];
    const slash = id.indexOf("/");
    return slash > 0 && slash < id.length - 1 ? map[id.slice(slash + 1)] : null;
  };
  const num = (v) =>
    v != null && Number.isFinite(Number(v)) ? Number(v) : null;

  const tracking = config?.cost_tracking;
  if (tracking?.enabled) {
    const cp = lookup(tracking.custom_pricing);
    if (cp) {
      return {
        source: "Cost Tracking",
        input: num(cp.input_per_mtok) ?? 0,
        output: num(cp.output_per_mtok) ?? 0,
      };
    }
  }
  const pricing = lookup(config?.model_database?.overrides)?.pricing;
  const perM = (v) => (num(v) == null ? null : num(v) * 1e6);
  const input = perM(pricing?.input_per_token);
  const output = perM(pricing?.output_per_token);
  if (input == null && output == null) return null;
  return { source: "Model Database", input, output };
}
