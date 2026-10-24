import { Box, Collapse, Drawer } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import EvaluationListPage from "../../../develop-detail/Evaluation/EvaluationListPage";
import EvaluationTypes from "../../../develop-detail/Common/EvaluationType/EvaluationTypes";
import ConfiguredEvaluationType from "../../../develop-detail/Common/ConfiguredEvaluationType/ConfiguredEvaluationType";
import EvaluationConfigureForm from "../../../develop-detail/Common/EvaluationConfigure/EvaluationConfigureForm";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const RunEvaluationChild = ({
  onClose,
  allColumns,
  refreshGrid,
  datasetId,
  experimentEval,
  hideSaveAndRun,
  setEvalsConfigs,
}) => {
  const [evaluationTypeOpen, setEvaluationTypeOpen] = useState(false);
  const [configureEvalOpen, setConfigureEvalOpen] = useState(false);
  const [selectedEval, setSelectedEval] = useState(null);

  return (
    <Box sx={{ display: "flex", height: "100%", justifyContent: "flex-end" }}>
      <Collapse in={evaluationTypeOpen} orientation="horizontal" unmountOnExit>
        <EvaluationTypes
          onClose={() => {
            setEvaluationTypeOpen(false);
          }}
          onOptionClick={(selectedEval) => {
            setEvaluationTypeOpen(false);
            setSelectedEval(selectedEval);
          }}
          datasetId={datasetId}
        />
      </Collapse>
      <Collapse
        in={Boolean(selectedEval)}
        orientation="horizontal"
        unmountOnExit
      >
        <EvaluationConfigureForm
          onClose={() => setSelectedEval(null)}
          onBackClick={(isPreviouslyConfigured) => {
            setSelectedEval(null);
            if (isPreviouslyConfigured) {
              setConfigureEvalOpen(true);
            } else {
              setEvaluationTypeOpen(true);
            }
          }}
          onSubmitComplete={() => {
            onClose();
          }}
          selectedEval={selectedEval}
          hideSaveAndRun={hideSaveAndRun}
          allColumns={allColumns}
          refreshGrid={refreshGrid}
          datasetId={datasetId}
          experimentEval={experimentEval}
          requiredColumnIds={
            experimentEval ? [experimentEval?.baseColumnId] : undefined
          }
        />
      </Collapse>
      <Collapse in={configureEvalOpen} orientation="horizontal">
        <ConfiguredEvaluationType
          onClose={() => {
            setConfigureEvalOpen(false);
          }}
          onOptionClick={(selectedEval) => {
            setConfigureEvalOpen(false);
            setSelectedEval({
              ...selectedEval,
              evalType: "previouslyConfigured",
            });
          }}
          datasetId={datasetId}
        />
      </Collapse>
      <EvaluationListPage
        onClose={onClose}
        setEvaluationTypeOpen={setEvaluationTypeOpen}
        setConfigureEvalOpen={setConfigureEvalOpen}
        refreshGrid={refreshGrid}
        datasetId={datasetId}
        experimentEval={experimentEval}
        setSelectedEval={setSelectedEval}
        setEvalsConfigs={setEvalsConfigs}
      />
    </Box>
  );
};

RunEvaluationChild.propTypes = {
  onClose: PropTypes.func,
  hideSaveAndRun: PropTypes.bool,
  allColumns: PropTypes.array,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  experimentEval: PropTypes.object,
  setEvalsConfigs: PropTypes.func,
};

const RunEvaluationsPrompt = ({
  open,
  onClose,
  allColumns,
  refreshGrid,
  datasetId,
  experimentEval,
  setEvalsConfigs,
  hideSaveAndRun,
}) => {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      sx={coveredDrawerSx(covered)}
      // onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <RunEvaluationChild
        onClose={onClose}
        allColumns={allColumns}
        refreshGrid={refreshGrid}
        datasetId={datasetId}
        experimentEval={experimentEval}
        setEvalsConfigs={setEvalsConfigs}
        hideSaveAndRun={hideSaveAndRun}
      />
    </Drawer>
  );
};

RunEvaluationsPrompt.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  hideSaveAndRun: PropTypes.bool,
  allColumns: PropTypes.array,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  experimentEval: PropTypes.object,
  setEvalsConfigs: PropTypes.func,
};

export default RunEvaluationsPrompt;
