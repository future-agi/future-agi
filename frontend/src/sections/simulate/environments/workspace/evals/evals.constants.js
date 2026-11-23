// Copy and shapes for the Evaluations tab, ported from the designer's
// EvalsStep / appliedEvals / AddEvalsDrawer. Strings live here rather than
// inline so the step, its rows and the tests share one source. The designer's
// twin-only surfaces (clone-state suggestions, "Author clone eval",
// TwinEvalEditor) are out of scope for this phase and are not ported.
import PropTypes from "prop-types";

export const EVALS_COPY = {
  required: "Required",
  requiredTip:
    "A failing score is flagged on the conversation beside its task result; it doesn't change the sub-goals' verdict.",
  requiredHint:
    "Required. A failing score is flagged on the conversation beside its task result; it doesn't change the sub-goals' verdict.",
  passAtLeast: "pass ≥",
  passAt: (n) => `pass ≥ ${n}%`,
  thresholdTip: "The score this evaluation needs to pass. Click to change it.",
  thresholdLabel: (name) => `Pass threshold for ${name}`,
  requiredLabel: (name) => `Required: ${name}`,
  costLabel: (item) => {
    const credits = item.credits_per_run;
    if (credits > 0)
      return `${credits} credit${credits === 1 ? "" : "s"} / call`;
    return item.charges_judge_tokens ? "Judge tokens" : "Free";
  },
  costTip: (item) => {
    const credits = item.credits_per_run;
    const judge = item.charges_judge_tokens;
    if (credits > 0)
      return `Metered at ${credits} credit${credits === 1 ? "" : "s"} per call graded${judge ? ", plus the judge's tokens" : ""}.`;
    return judge
      ? "No credits; the judge's tokens are charged."
      : "Runs without metering, so it's free.";
  },
  removeTitle: (name) => `Remove ${name || "this evaluation"}?`,
  removeBody: "New runs won't be graded on it; past scores stay.",
  add: "Add evaluations",
  addScenarios: "Add scenarios",
  toolCall: {
    title: "Enable tool call evaluation",
    on: "Tool calling that happens during the calls will be evaluated: the right tool, with the right arguments, at the right time.",
    off: "Connect your agent first. Tool calls are read from it during the run.",
    needsAgent: "Connect an agent to evaluate its tool calls.",
  },
  needsScenariosHint: "Add scenarios first",
  remove: "Remove evaluation",
  edit: "Edit evaluation",
  // A row the harness created and fills in itself: changing it would change
  // what later runs write into, so the server refuses the edit.
  notEditable: "Set by the harness, so it can't be edited here.",
  // An edit saved mid-grade would leave a run scored under two settings.
  gradingLocked: "Available once grading finishes.",
  suggestedTitle: (n) => `Suggested for this environment (${n})`,
  suggestedSubtitle:
    "Not added yet. Only the evaluations you add score a run. The scenarios' own checks always run.",
  addAll: "Add all",
  addOne: "Add",
  addedTitle: (n) => `Added evaluations (${n})`,
  // The same card while the server's list is unknown — a number here would be
  // a count of something nobody has read yet.
  addedTitleUnknown: "Added evaluations",
  addedError: "Couldn’t load this environment's evaluations. Try again.",
  addedSubtitle: (requiredCount) =>
    `Scored against these${requiredCount ? ` · ${requiredCount} required` : ""}.`,
  lockedTitle: "Add scenarios first",
  lockedBody:
    "An evaluation scores the tasks a run produces, so it needs scenarios to point at. Add some and this unlocks.",
  emptyTitle: "No evaluations added yet",
  emptyWithSuggestions:
    "Pick one from the suggestions above, or open the library for more.",
  emptyNoSuggestions:
    "Optional. Sub-goals decide pass or fail. Add evaluations to score each conversation.",
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
export const APPLIED_EVAL_SHAPE = PropTypes.oneOfType([
  PropTypes.string,
  EVAL_SHAPE,
]);

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
