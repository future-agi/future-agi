import React from "react";
import PropTypes from "prop-types";
import ErrorState from "src/components/error-state";

const PageErrorState = ({ message, onRetry }) => (
  <ErrorState
    detail={message || "Please try again."}
    onRetry={onRetry}
    sx={{ height: "auto", flexGrow: 0, py: 8 }}
  />
);

PageErrorState.propTypes = {
  message: PropTypes.string,
  onRetry: PropTypes.func,
};

export default PageErrorState;
