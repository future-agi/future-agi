import { Box, Button, Collapse, Drawer } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useEffect } from "react";
import EvaluationListPage from "./EvaluationListPage";
import EvaluationTypes from "../Common/EvaluationType/EvaluationTypes";
import ConfiguredEvaluationType from "../Common/ConfiguredEvaluationType/ConfiguredEvaluationType";
import EvaluationConfigureForm from "../Common/EvaluationConfigure/EvaluationConfigureForm";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { useGetJsonColumnSchema } from "src/api/develop/develop-detail";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
const RunEvaluationChild = ({
  onClose,
  allColumns,
  jsonSchemas = {},
  refreshGrid,
  datasetId,
  experimentEval,
  setConfirmationModalOpen,
  hideSaveAndRun,
  module,
  handleLabelsAdd,
  evalsConfigs,
  setEvalsConfigs = () => {},
  setFormIsDirty,
  formIsDirty,
  isConfirmationModalOpen,
  SetIsSelectedEval,
  openDrawer,
}) => {
  const [evaluationTypeOpen, setEvaluationTypeOpen] = useState(false);
  const [configureEvalOpen, setConfigureEvalOpen] = useState(false);
  const [selectedEval, setSelectedEval] = useState(null);

  useEffect(() => {
    SetIsSelectedEval?.(selectedEval);
  }, [selectedEval]);

  const handleConfirmClose = () => {
    setConfirmationModalOpen(false);
    onClose();
    setSelectedEval(false);
    setConfigureEvalOpen(false);
    setEvaluationTypeOpen(false);
  };

  const handleCancelClose = () => {
    setConfirmationModalOpen(false);
  };

  return (
    <Box
      sx={{
        display: "flex",
        height: "100%",
        justifyContent: "flex-end",
        backgroundColor: "background.paper",
      }}
    >
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
          handleLabelsAdd={handleLabelsAdd}
          module={module}
          evalsConfigs={evalsConfigs}
          setEvalsConfigs={setEvalsConfigs}
          allColumns={allColumns}
          jsonSchemas={jsonSchemas}
          refreshGrid={refreshGrid}
          datasetId={datasetId}
          experimentEval={experimentEval}
          requiredColumnIds={
            experimentEval ? [experimentEval?.baseColumnId] : undefined
          }
          setFormIsDirty={setFormIsDirty}
          setConfirmationModalOpen={setConfirmationModalOpen}
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
        onClose={() => {
          if (formIsDirty) {
            setConfirmationModalOpen(true);
          } else {
            onClose();
          }
        }}
        module={module}
        setEvaluationTypeOpen={setEvaluationTypeOpen}
        setConfigureEvalOpen={setConfigureEvalOpen}
        refreshGrid={refreshGrid}
        datasetId={datasetId}
        experimentEval={experimentEval}
        evalsConfigs={evalsConfigs}
        setSelectedEval={setSelectedEval}
        setEvalsConfigs={setEvalsConfigs}
        handleLabelsAdd={handleLabelsAdd}
        setFormIsDirty={setFormIsDirty}
        openDrawer={openDrawer}
      />
      <ConfirmDialog
        content="Are you sure you want to close? Your work will be lost"
        action={
          <Button
            variant="contained"
            color="error"
            onClick={handleConfirmClose}
          >
            Confirm
          </Button>
        }
        open={isConfirmationModalOpen}
        onClose={handleCancelClose}
        title="Confirm Action"
        message="Are you sure you want to proceed?"
      />
    </Box>
  );
};

RunEvaluationChild.propTypes = {
  onClose: PropTypes.func,
  hideSaveAndRun: PropTypes.bool,
  module: PropTypes.string,
  handleLabelsAdd: PropTypes.func,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  experimentEval: PropTypes.object,
  setEvalsConfigs: PropTypes.func,
  evalsConfigs: PropTypes.array,
  setFormIsDirty: PropTypes.func,
  formIsDirty: PropTypes.bool,
  setConfirmationModalOpen: PropTypes.func,
  isConfirmationModalOpen: PropTypes.bool,
  SetIsSelectedEval: PropTypes.func,
  openDrawer: PropTypes.bool,
};

const RunEvaluation = ({
  open,
  onClose,
  allColumns,
  refreshGrid,
  datasetId,
  experimentEval,
  evalsConfigs,
  setEvalsConfigs,
  hideSaveAndRun,
  module,
  handleLabelsAdd,
  SetIsSelectedEval,
}) => {
  const [formIsDirty, setFormIsDirty] = useState(false);
  const [isConfirmationModalOpen, setConfirmationModalOpen] = useState(false);

  // Fetch JSON schemas for JSON-type columns
  const { data: jsonSchemas = {} } = useGetJsonColumnSchema(datasetId, {
    enabled: open && Boolean(datasetId),
  });

  const handleClose = () => {
    if (formIsDirty) {
      setConfirmationModalOpen(true);
    } else {
      onClose();
    }
  };

  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      sx={coveredDrawerSx(covered)}
      onClose={handleClose}
      variant="persistent"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 2,
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
        jsonSchemas={jsonSchemas}
        refreshGrid={refreshGrid}
        datasetId={datasetId}
        experimentEval={experimentEval}
        evalsConfigs={evalsConfigs}
        setEvalsConfigs={setEvalsConfigs}
        hideSaveAndRun={hideSaveAndRun}
        handleLabelsAdd={handleLabelsAdd}
        module={module}
        formIsDirty={formIsDirty}
        setFormIsDirty={setFormIsDirty}
        setConfirmationModalOpen={setConfirmationModalOpen}
        isConfirmationModalOpen={isConfirmationModalOpen}
        SetIsSelectedEval={SetIsSelectedEval}
        openDrawer={open}
      />
    </Drawer>
  );
};

RunEvaluation.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  hideSaveAndRun: PropTypes.bool,
  handleLabelsAdd: PropTypes.func,
  module: PropTypes.string,
  allColumns: PropTypes.array,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  experimentEval: PropTypes.object,
  setEvalsConfigs: PropTypes.func,
  evalsConfigs: PropTypes.array,
  SetIsSelectedEval: PropTypes.func,
};

export default RunEvaluation;
