import React from "react";
import PropTypes from "prop-types";
import { Button } from "@mui/material";
import { CLASSIC_EXPERIMENT_COPY as COPY } from "src/launch/classicExperimentCopy";
import { DevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import RunExperiment from "src/sections/develop-detail/Experiment/RunExperiment";

// Existing PUT edits configurations and re-runs changed outputs. The separate
// append-variant door remains behind exp.rerun; this is not that action.
export default function ClassicEditExperiment({ experimentId, onSaved }) {
  return (
    <>
      <Button
        variant="outlined"
        onClick={() =>
          useRunExperimentStore.getState().initiateEditMode(experimentId)
        }
      >
        {COPY.editAndRerun}
      </Button>
      <DevelopDetailContext.Provider value={{ refreshGrid: onSaved }}>
        <RunExperiment onSuccessfulSubmit={onSaved} />
      </DevelopDetailContext.Provider>
    </>
  );
}
ClassicEditExperiment.propTypes = {
  experimentId: PropTypes.string.isRequired,
  onSaved: PropTypes.func,
};
