import PropTypes from "prop-types";

// `set` takes an input event, `put` a value.
export const ACTOR_FORM_SHAPE = PropTypes.shape({
  value: PropTypes.func.isRequired,
  set: PropTypes.func.isRequired,
  put: PropTypes.func.isRequired,
  problems: PropTypes.objectOf(PropTypes.string).isRequired,
  initial: PropTypes.object.isRequired,
});

export const OPTIONS_SHAPE = PropTypes.arrayOf(
  PropTypes.shape({ value: PropTypes.string, label: PropTypes.string }),
);
