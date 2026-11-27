import PropTypes from "prop-types";

// `value` reads a field, `put` writes one and marks it edited.
export const ACTOR_FORM_SHAPE = PropTypes.shape({
  value: PropTypes.func.isRequired,
  put: PropTypes.func.isRequired,
  problems: PropTypes.objectOf(PropTypes.string).isRequired,
  initial: PropTypes.object.isRequired,
});

export const OPTIONS_SHAPE = PropTypes.arrayOf(
  PropTypes.shape({
    value: PropTypes.string,
    label: PropTypes.string,
    // An option shown but not pickable, with a line saying why.
    disabled: PropTypes.bool,
    note: PropTypes.string,
  }),
);
