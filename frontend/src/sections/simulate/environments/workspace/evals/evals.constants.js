// Copy and shapes for the Evaluations tab, ported from the designer's
// EvalsStep / appliedEvals / AddEvalsDrawer. Strings live here rather than
// inline so the step, its rows and the tests share one source. The designer's
// twin-only surfaces (clone-state suggestions, "Author clone eval",
// TwinEvalEditor) are out of scope for this phase and are not ported.
import PropTypes from "prop-types";

export const EVALS_COPY = {
  heading: "Evaluations",
  intro:
    "These decide whether each task passed. Pick them from the library and map their inputs onto what the run produces.",
  add: "Add evaluations",
  addScenarios: "Add scenarios",
  needsScenariosHint: "Add scenarios first",
  remove: "Remove evaluation",
  suggestedTitle: (n) => `Suggested evaluations (${n})`,
  suggestedSubtitle:
    "The environment thinks these would matter — add the ones you want the run scored against.",
  addAll: (n) => `Add all ${n}`,
  addOne: "Add",
  addedTitle: (n) => `Added evaluations (${n})`,
  addedSubtitle:
    "Every task is scored against these. Add more from Suggested or the library any time.",
  lockedTitle: "Add scenarios first",
  lockedBody:
    "An evaluation scores the tasks a run produces, so it needs scenarios to point at. Add some and this unlocks.",
  emptyTitle: "No evaluations added yet",
  emptyWithSuggestions: "Pick one from the suggestions above, or open the library for more.",
  emptyNoSuggestions:
    "You can run without them — you'll get traces, but nothing will tell you whether the agent was right.",
  // Fallbacks the picker's returned config is normalised against.
  pickerBlurb: "Added from the eval library",
  pickerFallbackName: "Eval",
};

export const EVAL_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  name: PropTypes.string,
  category: PropTypes.string,
  blurb: PropTypes.string,
  icon: PropTypes.string,
  color: PropTypes.string,
  type: PropTypes.string,
  threshold: PropTypes.number,
  mapping: PropTypes.objectOf(PropTypes.string),
  model: PropTypes.oneOfType([PropTypes.string, PropTypes.shape({})]),
  custom: PropTypes.bool,
});

// Applied evals are stored either as bare ids (older state) or configured
// objects (picker adds), so the slice accepts both shapes.
export const APPLIED_EVAL_SHAPE = PropTypes.oneOfType([PropTypes.string, EVAL_SHAPE]);

export const ENV_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  name: PropTypes.string,
  surface: PropTypes.string,
  evalPreset: PropTypes.arrayOf(PropTypes.string),
});

export const ENV_STATE_SHAPE = PropTypes.shape({
  evals: PropTypes.arrayOf(APPLIED_EVAL_SHAPE),
  scenarios: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string })),
  agent: PropTypes.shape({ typeId: PropTypes.string }),
});
