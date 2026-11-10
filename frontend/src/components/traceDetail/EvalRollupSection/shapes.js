import PropTypes from "prop-types";

export const evalRollupShape = PropTypes.shape({
  scope: PropTypes.oneOf(["trace", "span"]),
  error: PropTypes.bool,
  evals: PropTypes.arrayOf(PropTypes.object),
});
