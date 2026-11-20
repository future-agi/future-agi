import React from "react";
import PropTypes from "prop-types";
import EmptyContent from "src/components/empty-content";

export default function NoResultsUI({
  title = "No evaluations has been logged",
  description = "Test evaluations on the playground section to view the logs",
}) {
  return (
    <EmptyContent
      title={title}
      description={description || undefined}
      sx={{ minHeight: 400 }}
    />
  );
}

NoResultsUI.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
};

NoResultsUI.displayName = "NoResultsUI";
