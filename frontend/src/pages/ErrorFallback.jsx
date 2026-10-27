import PropTypes from "prop-types";
import React from "react";
import { Helmet } from "react-helmet-async";
import ErrorFallbackView from "src/sections/error/ErrorFallbackView";

const ErrorFallback = ({ error, resetErrorBoundary }) => {
  return (
    <>
      <Helmet>
        <title>Something went wrong</title>
      </Helmet>
      <ErrorFallbackView
        error={error}
        resetErrorBoundary={resetErrorBoundary}
      />
    </>
  );
};

export default ErrorFallback;

ErrorFallback.propTypes = {
  error: PropTypes.any,
  resetErrorBoundary: PropTypes.func,
};
