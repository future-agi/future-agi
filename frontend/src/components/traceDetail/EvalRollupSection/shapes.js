import PropTypes from "prop-types";

export const evalSpanShape = PropTypes.shape({
  span_id: PropTypes.string,
  span_name: PropTypes.string,
  value: PropTypes.oneOfType([
    PropTypes.number,
    PropTypes.string,
    PropTypes.arrayOf(PropTypes.string),
  ]),
  explanation: PropTypes.string,
  error: PropTypes.bool,
});

export const evalShape = PropTypes.shape({
  eval_config_id: PropTypes.string,
  eval_name: PropTypes.string,
  output_type: PropTypes.string,
  target_type: PropTypes.string,
  aggregate: PropTypes.oneOfType([
    PropTypes.number,
    PropTypes.objectOf(PropTypes.number),
  ]),
  spans: PropTypes.arrayOf(evalSpanShape),
  choices_map: PropTypes.objectOf(PropTypes.string),
});

export const evalTaskShape = PropTypes.shape({
  eval_task_id: PropTypes.string,
  eval_task_name: PropTypes.string,
  evals: PropTypes.arrayOf(evalShape),
});

export const evalScoresShape = PropTypes.shape({
  scope: PropTypes.string,
  eval_tasks: PropTypes.arrayOf(evalTaskShape),
});
