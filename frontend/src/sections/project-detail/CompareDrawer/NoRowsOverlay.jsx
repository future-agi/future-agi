import PropTypes from "prop-types";
import React from "react";
import EmptyContent from "src/components/empty-content";

// Called as a function by AG Grid overlay callers: NoRowsOverlay("No x found").
const NoRowsOverlay = (title) => {
  return <EmptyContent title={title} />;
};

NoRowsOverlay.propTypes = {
  title: PropTypes.node,
};

export default NoRowsOverlay;
