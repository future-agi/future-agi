import { isSurfaceShown } from "src/launch/launchSwitch";

// What the picker's version choice does on each host:
// - "pick": the host saves the picked version and runs it (the dataset,
//   experiment and optimization drawers);
// - "default only": the host sends no version and runs the evaluation's
//   current settings (the task, simulation and workbench hosts), so the
//   choice names no version and cannot be changed.
// Any other source shows no version choice.
export const VERSION_CHOICE_BY_SOURCE = Object.freeze({
  dataset: "pick",
  experiment: "pick",
  "run-optimization": "pick",
  task: "default only",
  simulation: "default only",
  "create-simulate": "default only",
  workbench: "default only",
});

// Shown beside a version choice that cannot be changed.
export const DEFAULT_VERSION_RUNS_LINE =
  "A version can't be chosen here. This evaluation runs with its current settings.";

// An experiment's new binding leaves the pick out of its save while the new
// experiment pages are off (classicExperimentEvalSelection in
// src/launch/experimentExecutionPin.js), so there it offers no choice unless
// an existing binding is edited.
export function versionChoiceFor(source, { editingBinding = false } = {}) {
  if (source === "experiment" && !editingBinding && !isSurfaceShown("exp.new"))
    return null;
  return VERSION_CHOICE_BY_SOURCE[source] ?? null;
}
