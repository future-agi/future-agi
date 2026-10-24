import { unitWord } from "src/sections/projects/observePopulation";
import {
  CUSTOM,
  datePresetOf,
  defaultScope,
  presetWindow,
  scopeFromSeed,
  scopeLabel,
  splitWindow,
  windowItem,
} from "src/sections/projects/observeScope";
import { handedIds } from "./askPopulation";

/**
 * What a new question reads: the rows handed over by id (a selection, fixed),
 * else the hand-over's date and filters, else the past week.
 */
export function seedScope(seed, unit, now = new Date()) {
  if (!seed) return defaultScope(unit, now);
  const scope = scopeFromSeed(seed, unit, now);
  const ids = handedIds(seed);
  if (!ids) return scope;
  return {
    ...scope,
    ids,
    label:
      seed.label ||
      `${ids.length.toLocaleString()} selected ${unitWord(unit, ids.length)}`,
  };
}

/**
 * The scope a finished run starts again with. A preset's window moves to
 * now, so "Past 7D" means the past seven days from this moment; a custom
 * range, and a run from before presets were kept, read the same window.
 * Rows named by id are read again as they are (users in the same window).
 */
export function rerunScope(scope = {}, fallback = {}, now = new Date()) {
  const label = scope.label || fallback.label;
  if (scope.ids?.length)
    return {
      filters: scope.filters || [],
      window: fallback.window ?? null,
      label,
    };
  const preset = scope.date_preset;
  const fresh = preset && preset !== CUSTOM ? presetWindow(preset, now) : null;
  const { window, filters } = splitWindow(scope.filters);
  if (!fresh)
    return {
      filters: scope.filters || [],
      window: window || fallback.window || null,
      datePreset: datePresetOf(preset),
      label,
    };
  return {
    filters: [...filters, windowItem(fresh)],
    window: fresh,
    datePreset: preset,
    label: scopeLabel({ dateOption: preset, window: fresh, filters }),
  };
}
