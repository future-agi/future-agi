import PropTypes from "prop-types";

// Local PropTypes shapes for the Scenarios tab. Kept in the scenarios folder
// (not workspace.shapes.js) so this Wave-2 slice owns its own contracts.

export const PERSONA_SHAPE = PropTypes.shape({
  name: PropTypes.string,
  slug: PropTypes.string,
  role: PropTypes.string,
  gender: PropTypes.string,
  ageGroup: PropTypes.string,
  traits: PropTypes.arrayOf(PropTypes.string),
});

export const SUB_TASK_SHAPE = PropTypes.oneOfType([
  PropTypes.string,
  PropTypes.shape({
    id: PropTypes.string,
    label: PropTypes.string,
    text: PropTypes.string,
    title: PropTypes.string,
  }),
]);

export const SCENARIO_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  name: PropTypes.string,
  title: PropTypes.string,
  summary: PropTypes.string,
  useCase: PropTypes.string,
  task: PropTypes.string,
  situation: PropTypes.string,
  expected: PropTypes.string,
  outcome: PropTypes.string,
  turns: PropTypes.number,
  critical: PropTypes.bool,
  persona: PERSONA_SHAPE,
  subTasks: PropTypes.arrayOf(SUB_TASK_SHAPE),
  conversationBranch: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.arrayOf(PropTypes.string),
  ]),
  branchCategory: PropTypes.string,
});

export const USE_CASE_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  label: PropTypes.string,
});

export const GROUP_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  label: PropTypes.string,
  rows: PropTypes.arrayOf(SCENARIO_SHAPE),
});

export const ENV_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  name: PropTypes.string,
  tools: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string, desc: PropTypes.string }),
  ),
  rules: PropTypes.arrayOf(PropTypes.string),
});

// Only the slice this tab reads. The full workspace env-state shape lands with
// the shared workspace.shapes.js in a later wave.
export const ENV_STATE_SHAPE = PropTypes.shape({
  scenarios: PropTypes.arrayOf(SCENARIO_SHAPE),
});
