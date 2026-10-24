import React, { useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Drawer,
  useTheme,
  CircularProgress,
  Collapse,
} from "@mui/material";
import EvaluationsSelectionGrid from "../../sections/common/EvaluationDrawer/EvaluationsSelectionGrid";
import EvaluationMappingForm from "../../sections/common/EvaluationDrawer/EvaluationMappingForm";
import EvaluationProvider from "../../sections/common/EvaluationDrawer/context/EvaluationProvider";
import { useEvaluationContext } from "../../sections/common/EvaluationDrawer/context/EvaluationContext";
import CustomEvalsForm from "src/sections/common/EvaluationDrawer/CustomEvalsForm";
import CreateEvaluationGroupDrawer from "../../sections/common/EvaluationDrawer/CreateEvaluationGroupDrawer";
import { resetEvalStore } from "../../sections/evals/store/useEvalStore";
import { useScenarioColumnConfig } from "src/sections/test/common";
import { voiceEvalColumns, chatEvalColumns } from "./common";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const EvaluationSelectionContent = ({
  onClose,
  onAddEvaluation,
  open,
  selectedEvalItem,
  scenarioColumnConfig,
  onConfigBack,
  agentType,
  allColumns: allColumnsProp,
  module: moduleProp = "create-simulate",
  defaultWidth = "100%",
}) => {
  const theme = useTheme();
  const {
    visibleSection,
    setVisibleSection,
    selectedEval,
    setModule,
    setActionButtonConfig,
    setCurrentTab,
    setSelectedGroup,
    setSelectedEval,
  } = useEvaluationContext();

  const updatedEvalData = useMemo(() => {
    if (!selectedEvalItem) return null;

    return {
      ...selectedEvalItem,
      mapping: selectedEvalItem?.config?.mapping || selectedEvalItem?.mapping,
      config: selectedEvalItem?.config,
      id: selectedEvalItem?.id || selectedEvalItem?.templateId,
      templateId: selectedEvalItem?.templateId || selectedEvalItem?.id,
      selectedModel: selectedEvalItem?.model,
      models: selectedEvalItem?.models,
      templateName:
        selectedEvalItem?.templateName || selectedEvalItem?.evalTemplateName,
      ...(selectedEvalItem?.groupName && {
        requiredKeys:
          selectedEvalItem?.evalRequiredKeys || selectedEvalItem?.requiredKeys,
        optionalKeys:
          selectedEvalItem?.optionalKeys || selectedEvalItem?.optionKeys,
      }),
    };
  }, [selectedEvalItem]);
  if (
    selectedEvalItem &&
    (!selectedEval || selectedEval?.id !== updatedEvalData?.id)
  ) {
    setSelectedGroup(null);
    setSelectedEval(updatedEvalData);
  }

  useEffect(() => {
    if (!open) {
      return;
    }
    setModule(moduleProp);
    setActionButtonConfig({
      id: "bb36008d-9e48-4cc5-87d9-eae3fc215365",
      showTest: false,
      showAdd: true,
      testLabel: "Test",
      runLabel: "Add Evaluation",
      handleRun: (data, onSuccess) => {
        onSuccess?.();
      },
      handleTest: () => {},
    });

    setVisibleSection("config");
  }, [open, moduleProp, setModule, setActionButtonConfig, setVisibleSection]);

  useEffect(() => {
    return () => {
      if (!selectedEvalItem) {
        setSelectedEval(null);
      }
    };
  }, [selectedEvalItem, setSelectedEval]);
  const baseEvalColumns = useMemo(
    () => (agentType === AGENT_TYPES.CHAT ? chatEvalColumns : voiceEvalColumns),
    [agentType],
  );
  const scenarioBasedColumns = useScenarioColumnConfig(baseEvalColumns, {
    scenariosDetail: scenarioColumnConfig ?? [],
  });
  const evalColumns = allColumnsProp ?? scenarioBasedColumns;
  const handleEvaluationSubmit = (
    formData,
    removedEvals = [],
    evalConfig = {},
  ) => {
    const newEvaluation = {
      ...selectedEval,
      ...formData,
      templateName: evalConfig?.templateName || selectedEval?.templateName,
      mapping: formData?.config?.mapping,
      requiredKeys: evalConfig?.requiredKeys || selectedEval?.requiredKeys,
      optionalKeys: evalConfig?.optionalKeys || selectedEval?.optionalKeys,
      id: selectedEval?.id,
      config: formData?.config,

      evalId: `${selectedEval.id}_${Date.now()}`,
      models: evalConfig?.models,
      templateId: selectedEval?.templateId ?? selectedEval?.id,
      ...(selectedEvalItem && {
        previousId: selectedEvalItem?.evalId,
      }),
      ...(removedEvals?.length > 0 && { removedEvals }),
      ...(selectedEval?.groupName && {
        isGroupEvals: false,
        removableId: selectedEvalItem?.evalId,
        model: selectedEvalItem?.model,
      }),
      model: formData?.model,
    };
    onAddEvaluation(newEvaluation);
    onClose();
    setCurrentTab("evals");
    setSelectedGroup(null);
    setVisibleSection("config");
  };

  if (!theme) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100%",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (selectedEvalItem && updatedEvalData) {
    return (
      <Box sx={{ width: defaultWidth, height: "100%" }}>
        <EvaluationMappingForm
          onClose={() => {
            setCurrentTab("evals");
            setSelectedGroup(null);
            onClose();
          }}
          onBack={() => {
            onClose();
          }}
          fullWidth={defaultWidth}
          allColumns={evalColumns}
          onFormSave={handleEvaluationSubmit}
          hideTitle={false}
          hideBackButtons={false}
          module="create-simulate"
          id="bb36008d-9e48-4cc5-87d9-eae3fc215365"
          refreshGrid={undefined}
          requiredColumnIds={undefined}
          saveGroup
          selectedEvalItem={updatedEvalData}
        />
      </Box>
    );
  }

  return (
    <Box sx={{ width: "100%", height: "100%", display: "flex" }}>
      <Collapse
        in={visibleSection === "config"}
        orientation="horizontal"
        sx={{
          display: "flex",
          flexDirection: "column",
          flexGrow: 1,
          gap: theme.spacing(2),
        }}
      >
        <EvaluationsSelectionGrid
          onClose={onClose}
          showRecommendations={false}
          datasetId="bb36008d-9e48-4cc5-87d9-eae3fc215365"
          hideHeadings={false}
          isEvalsView={false}
          theme={theme}
          order="simulate"
          onConfigBack={onConfigBack}
        />
      </Collapse>
      <Collapse
        in={visibleSection === "custom"}
        orientation="horizontal"
        sx={{
          display: "flex",
          flexDirection: "column",
          flexGrow: 1,
          gap: theme.spacing(2),
        }}
      >
        <CustomEvalsForm
          onClose={() => {
            onClose();
            setVisibleSection("config");
          }}
        />
      </Collapse>
      <Collapse in={visibleSection === "create-group"} orientation="horizontal">
        <CreateEvaluationGroupDrawer
          open={visibleSection === "create-group"}
          handleClose={() => {
            setVisibleSection("config");
          }}
          onBack={() => {
            resetEvalStore();
            setCurrentTab("evals");
            setVisibleSection("config");
          }}
          isEvalsView={false}
        />
      </Collapse>
      <Collapse
        in={visibleSection === "mapping"}
        orientation="horizontal"
        unmountOnExit
        sx={{
          display: "flex",
          flexDirection: "column",
          flexGrow: 1,
          gap: theme.spacing(2),
        }}
      >
        <Box sx={{ width: defaultWidth, height: "100%" }}>
          <EvaluationMappingForm
            onClose={() => {
              setCurrentTab("evals");
              setSelectedGroup(null);
              onClose();
            }}
            fullWidth={defaultWidth}
            onBack={() => setVisibleSection("config")}
            allColumns={evalColumns}
            onFormSave={handleEvaluationSubmit}
            hideTitle={false}
            hideBackButtons={false}
            module={moduleProp}
            id="bb36008d-9e48-4cc5-87d9-eae3fc215365"
            refreshGrid={undefined}
            requiredColumnIds={undefined}
            saveGroup
            selectedEvalItem={selectedEvalItem}
          />
        </Box>
      </Collapse>
    </Box>
  );
};

EvaluationSelectionContent.propTypes = {
  onClose: PropTypes.func.isRequired,
  onAddEvaluation: PropTypes.func.isRequired,
  datasetId: PropTypes.string,
  open: PropTypes.bool,
  selectedEvalItem: PropTypes.object,
  scenarioColumnConfig: PropTypes.array,
  onConfigBack: PropTypes.func,
  agentType: PropTypes.string,
  allColumns: PropTypes.array,
  module: PropTypes.string,
  defaultWidth: PropTypes.string,
};

const EvaluationSelectionDialog = ({
  open,
  onClose,
  onAddEvaluation,
  datasetId,
  selectedEvalItem,
  scenarioColumnConfig,
  onConfigBack,
  agentType,
  allColumns,
  module,
  defaultWidth = "100%",
}) => {
  const covered = useDrawerCovered(open);
  useEffect(() => {
    return () => {
      resetEvalStore();
    };
  }, []);

  return (
    <EvaluationProvider>
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
        sx={coveredDrawerSx(covered)}
        PaperProps={{
          sx: {
            padding: 2,
            height: "100vh",
            backgroundColor: "background.paper",
            borderRadius: "4px !important",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: {
              borderRadius: "0px !important",
              backgroundColor: "transparent",
            },
          },
        }}
      >
        <EvaluationSelectionContent
          onClose={onClose}
          onAddEvaluation={onAddEvaluation}
          datasetId={datasetId}
          open={open}
          selectedEvalItem={selectedEvalItem}
          scenarioColumnConfig={scenarioColumnConfig}
          onConfigBack={onConfigBack}
          agentType={agentType}
          allColumns={allColumns}
          module={module}
          defaultWidth={defaultWidth}
        />
      </Drawer>
    </EvaluationProvider>
  );
};

EvaluationSelectionDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onAddEvaluation: PropTypes.func.isRequired,
  datasetId: PropTypes.string,
  selectedEvalItem: PropTypes.object,
  scenarioColumnConfig: PropTypes.array,
  onConfigBack: PropTypes.func,
  agentType: PropTypes.string,
  allColumns: PropTypes.array,
  module: PropTypes.string,
  defaultWidth: PropTypes.string,
};

export default EvaluationSelectionDialog;
