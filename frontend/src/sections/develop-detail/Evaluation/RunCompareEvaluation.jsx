import { Box, Button, Collapse, Drawer } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import EvaluationTypes from "../Common/EvaluationType/EvaluationTypes";
import ConfiguredEvaluationType from "../Common/ConfiguredEvaluationType/ConfiguredEvaluationType";
import EvaluationConfigureForm from "../Common/EvaluationConfigure/EvaluationConfigureForm";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import CompareEvaluationListPage from "./CompareEvaluationListPage";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import logger from "src/utils/logger";
import { useGetJsonColumnSchema } from "src/api/develop/develop-detail";
import { isMappingPath } from "src/sections/evals/utils/evalMappingPath";
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
  selectedDatasets,
  isCompareDataset,
  datasetInfo,
  commonColumn,
  baseColumn,
  compareRefreshGrid,
}) => {
  const [evaluationTypeOpen, setEvaluationTypeOpen] = useState(false);
  const [configureEvalOpen, setConfigureEvalOpen] = useState(false);
  const [selectedEval, setSelectedEval] = useState(null);
  const queryClient = useQueryClient();

  const { mutate: SaveCompareEval, isPending: SaveCompareEvalLoading } =
    useMutation({
      mutationFn: (d) => {
        return axios.post(endpoints.develop.eval.addCompareEval(datasetId), d);
      },
      onSuccess: () => {
        enqueueSnackbar("Evaluation added successfully", {
          variant: "success",
        });
        compareRefreshGrid({ route: [] }, true);
        queryClient.invalidateQueries([
          "develop",
          "compare-user-eval-list",
          selectedDatasets,
          isCompareDataset,
        ]);
      },
      onError: (err) => {
        logger.error("Failed to save compare eval", err);
      },
    });

  const handleFormSave = (data) => {
    if (data?.run == true) {
      onClose();
    }
    const fieldMapping = new Map(
      allColumns.map((col) => [col.field, col.headerName]),
    );
    // Replace all mapping field IDs with header names
    // Handle JSON paths: "uuid.path" -> "HeaderName.path"
    data.config.mapping = Object.fromEntries(
      Object.entries(data.config.mapping).map(([key, value]) => {
        // A non-string mapping value has no field id to rewrite, and .match()
        // on one throws inside this save handler — no error boundary catches an
        // event handler, so the save button would just stop working. Forward it
        // untouched and let the API reject it with a message.
        if (!isMappingPath(value)) return [key, value];

        // Check if value contains a JSON path (UUID format followed by dot)
        const uuidPattern =
          /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(.+)$/i;
        const match = value.match(uuidPattern);

        if (match) {
          // Extract base UUID and JSON path
          const baseFieldId = match[1];
          const jsonPath = match[2];
          const headerName = fieldMapping.get(baseFieldId);
          // Return headerName.jsonPath if found, otherwise keep original
          return [key, headerName ? `${headerName}.${jsonPath}` : value];
        }

        // No JSON path, just convert field ID to header name
        return [key, fieldMapping.get(value) || value];
      }),
    );
    data.dataset_ids = selectedDatasets;
    data.dataset_info = datasetInfo;
    data.common_column_names = commonColumn;
    data.base_column_name = baseColumn;
    data.template_id = selectedEval?.id;
    data.run = data.run || false;

    SaveCompareEval(data);

    setEvaluationTypeOpen(false);
    setConfigureEvalOpen(false);
    setSelectedEval(false);
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
          module="observe"
          evalsConfigs={evalsConfigs}
          setEvalsConfigs={setEvalsConfigs}
          allColumns={allColumns}
          jsonSchemas={jsonSchemas}
          refreshGrid={() => {}}
          datasetId={datasetId}
          // experimentEval={experimentEval}
          requiredColumnIds={[]}
          onFormSave={(data) => handleFormSave(data)}
          loadingStates={{ saveLoading: SaveCompareEvalLoading }}
          // setFormIsDirty={setFormIsDirty}
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
      <CompareEvaluationListPage
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
        selectedDatasets={selectedDatasets}
        isCompareDataset={isCompareDataset}
        compareRefreshGrid={compareRefreshGrid}
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
  selectedDatasets: PropTypes.array,
  isCompareDataset: PropTypes.bool,
  datasetInfo: PropTypes.array,
  commonColumn: PropTypes.array,
  baseColumn: PropTypes.string,
  compareRefreshGrid: PropTypes.func,
};

const RunCompareEvaluation = ({
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
  selectedDatasets,
  isCompareDataset,
  datasetInfo,
  commonColumn,
  baseColumn,
  compareRefreshGrid,
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

  const handleConfirmClose = () => {
    setConfirmationModalOpen(false);
    onClose();
  };

  const handleCancelClose = () => {
    setConfirmationModalOpen(false);
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      variant="persistent"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 2,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "10px",
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
        selectedDatasets={selectedDatasets}
        isCompareDataset={isCompareDataset}
        datasetInfo={datasetInfo}
        commonColumn={commonColumn}
        baseColumn={baseColumn}
        compareRefreshGrid={compareRefreshGrid}
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
    </Drawer>
  );
};

RunCompareEvaluation.propTypes = {
  compareRefreshGrid: PropTypes.func,
  baseColumn: PropTypes.string,
  datasetInfo: PropTypes.array,
  commonColumn: PropTypes.array,
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
  selectedDatasets: PropTypes.array,
  isCompareDataset: PropTypes.bool,
};

export default RunCompareEvaluation;
