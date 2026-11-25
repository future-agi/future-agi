import React from "react";
import PropTypes from "prop-types";
import { Button } from "@mui/material";
import EmptyState from "src/components/empty-state";

/**
 * The right pane before the first report. New report opens the creation
 * flow, where the lens is picked; the rail lists the templates too.
 */
export default function InsightsEmpty({ onNew }) {
  return (
    <EmptyState
      icon="solar:chart-square-linear"
      title="No reports yet"
      description="A report reads your traffic, groups it into categories and cites the conversations behind every number."
      action={
        <Button
          variant="contained"
          color="primary"
          size="small"
          onClick={() => onNew()}
        >
          New report
        </Button>
      }
    />
  );
}

InsightsEmpty.propTypes = { onNew: PropTypes.func.isRequired };
