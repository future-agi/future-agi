import {
  Drawer,
  Typography,
  Stack,
  Divider,
  IconButton,
  Box,
  Button,
  CircularProgress,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useEffect, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useForm, FormProvider } from "react-hook-form";
import { useParams, useSearchParams } from "react-router-dom";
import { UploadJsonDialog } from "src/components/upload-json-dialog";
import ManualVariablesForm from "./ManualVariablesForm";
import { useGlobalVariablesDrawerStoreShallow, VIEW } from "../../store";
import UploadedJSON from "./UploadedJSON";
import HeaderActions from "./HeaderActions";
import SvgColor from "src/components/svg-color";
// import ImportDatasetDrawer from "src/components/VariableDrawer/ImportDataset/ImportDatasetDrawer";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { useGetGraphDataset } from "../../../../api/agent-playground/agent-playground";
import EmptyVariable from "src/components/VariableDrawer/EmptyVariable";
import { escapeModelKey } from "src/sections/develop-detail/Experiment/utils";

// Dummy API call for uploading JSON - replace with actual endpoint
const uploadGlobalVariablesJson = async (file) => {
  // Simulate API delay
  await new Promise((resolve) => setTimeout(resolve, 1500));

  // Read and parse JSON file
  const text = await file.text();
  const jsonData = JSON.parse(text);

  // TODO: Replace with actual API call
  // return axios.post(endpoints.globalVariables.upload, { data: jsonData });

  return { success: true, data: jsonData };
};

/**
 * Escape dots in object keys to prevent RHF from interpreting them as nested paths.
 */
function escapeKeys(obj) {
  const result = {};
  for (const [k, v] of Object.entries(obj)) {
    result[escapeModelKey(k)] = v;
  }
  return result;
}

/**
 * Derive form variables from dataset API response.
 * Maps columns to keys and first row's cells to values.
 * Returns cellMap: { [columnName]: cellObject } for save lookups.
 */
function deriveVariablesFromDataset(dataset) {
  if (!dataset?.columns?.length) return { variables: {}, cellMap: {} };
  const row = dataset.rows?.[0] || null;
  const variables = {};
  const cellMap = {};
  for (const col of dataset.columns) {
    const cell = row?.cells?.find((c) => c.columnId === col.id);
    variables[col.name] = cell?.value ?? "";
    if (cell) cellMap[col.name] = cell;
  }
  return { variables, cellMap };
}

function Header({
  onClose,
  handleUploadJson,
  showHeaderActions,
  currentView,
  onOpenImportDatasetDrawer,
  disabled,
}) {
  return (
    <Stack
      direction="row"
      justifyContent="space-between"
      alignItems="flex-start"
      gap={2}
    >
      <Stack direction="column" gap={0.25}>
        <Typography
          typography="m3"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          Variables
        </Typography>
        <Typography
          typography="s1"
          fontWeight="fontWeightRegular"
          color="text.secondary"
        >
          Define values for your prompt variables
        </Typography>
      </Stack>
      <Stack direction={"row"} alignItems={"center"} gap={1.5}>
        {showHeaderActions && (
          <HeaderActions
            handleUploadJson={handleUploadJson}
            currentView={currentView}
            onOpenImportDatasetDrawer={onOpenImportDatasetDrawer}
            disabled={disabled}
          />
        )}
        <IconButton onClick={onClose}>
          <SvgColor
            // @ts-ignore
            src="/assets/icons/ic_close.svg"
            sx={{ height: "24px", width: "24px", bgcolor: "text.primary" }}
          />
        </IconButton>
      </Stack>
    </Stack>
  );
}

Header.propTypes = {
  onClose: PropTypes.func.isRequired,
  handleUploadJson: PropTypes.func.isRequired,
  showHeaderActions: PropTypes.bool,
  currentView: PropTypes.string,
  onOpenImportDatasetDrawer: PropTypes.func,
  disabled: PropTypes.bool,
};

export default function GlobalVariableDrawer({ open, onClose }) {
  const { agentId } = useParams();
  const [searchParams] = useSearchParams();
  const graphId = agentId;
  const versionId = searchParams.get("version");

  const [showUploadJsonDialog, setShowUploadJsonDialog] = useState(false);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);

  const {
    setUploadedJson,
    uploadedJson,
    uploadedFileName,
    currentView,
    setCurrentView,
    setGlobalVariables,
    // importDatasetDrawerOpen,
    // setImportDatasetDrawerOpen,
    setPendingRun,
  } = useGlobalVariablesDrawerStoreShallow((state) => ({
    setUploadedJson: state.setUploadedJson,
    uploadedJson: state.uploadedJson,
    uploadedFileName: state.uploadedFileName,
    currentView: state.currentView,
    setCurrentView: state.setCurrentView,
    setGlobalVariables: state.setGlobalVariables,
    // importDatasetDrawerOpen: state.importDatasetDrawerOpen,
    // setImportDatasetDrawerOpen: state.setImportDatasetDrawerOpen,
    setPendingRun: state.setPendingRun,
  }));

  // Fetch dataset when drawer is open — always refetch so variables are fresh
  const { data: datasetData, isLoading: isDatasetLoading } = useGetGraphDataset(
    graphId,
    versionId,
    { enabled: open, refetchOnMount: "always", staleTime: 0 },
  );

  // Derive variables and cellMap from dataset
  const { variables: datasetVariables, cellMap } = useMemo(
    () => deriveVariablesFromDataset(datasetData),
    [datasetData],
  );

  // Sync dataset variables to store (for other consumers like useConnectedNodeVariables)
  useEffect(() => {
    if (datasetData && Object.keys(datasetVariables).length > 0) {
      setGlobalVariables(datasetVariables);
    }
  }, [datasetVariables, datasetData, setGlobalVariables]);

  // Form management — use API-derived data as source of truth, not the store
  const methods = useForm({
    defaultValues: escapeKeys(datasetVariables),
    mode: "onChange",
  });

  const {
    watch,
    reset,
    // setValue,
    formState: { isDirty },
  } = methods;

  // Watch all form values
  const formValues = watch();

  // Sync form when API-derived variables change
  useEffect(() => {
    reset(escapeKeys(datasetVariables));
  }, [datasetVariables, reset]);

  // Get variable keys for the ImportDatasetDrawer
  const variableKeys = Object.keys(datasetVariables);

  // Handler called when ImportDatasetDrawer applies data
  // Using getValues instead of watch() to avoid new object reference on every render
  // const handleSetVariableData = useCallback(
  //   (variableData) => {
  //     // importedData is an object like { variableName: [values...] }
  //     // We take the first value from each array for the form
  //     const currentValues = getValues();
  //     Object.entries(variableData).forEach(([key, values]) => {
  //       if (key in currentValues && Array.isArray(values) && values.length > 0) {
  //         setValue(key, values[0], { shouldDirty: true });
  //       }
  //     });
  //   },
  //   [getValues, setValue],
  // );

  // Derive view from uploadedJson (store takes priority)
  const activeView = uploadedJson ? VIEW.UPLOADED_JSON : currentView;

  // Mutation for uploading JSON file
  const uploadMutation = useMutation({
    mutationFn: uploadGlobalVariablesJson,
  });

  // Handle successful upload - receives file from dialog
  const handleUploadSuccess = (file) => {
    if (uploadMutation.data) {
      setUploadedJson(uploadMutation.data.data, file?.name);
    }
    setShowUploadJsonDialog(false);
    uploadMutation.reset();
  };

  const handleClose = () => {
    if (isDirty && activeView === VIEW.MANUAL_FORM) {
      setShowConfirmDialog(true);
    } else {
      confirmClose();
    }
  };

  const confirmClose = () => {
    setCurrentView(VIEW.ACTIONS);
    setShowUploadJsonDialog(false);
    setShowConfirmDialog(false);
    setPendingRun(false);
    reset(escapeKeys(datasetVariables)); // Reset form to clean state
    uploadMutation.reset();
    onClose();
  };

  const cancelClose = () => {
    setShowConfirmDialog(false);
  };

  const handleUploadJson = () => {
    setShowUploadJsonDialog(true);
  };

  const handleUploadJsonClose = () => {
    setShowUploadJsonDialog(false);
    uploadMutation.reset();
  };

  const handleFileSelect = (file) => {
    uploadMutation.mutate(file);
  };

  // const handleOpenImportDatasetDrawer = () => {
  //   setImportDatasetDrawerOpen(true);
  // };

  // const handleCloseImportDatasetDrawer = () => {
  //   setImportDatasetDrawerOpen(false);
  // };

  const renderContent = () => {
    if (datasetData?.columns?.length === 0) {
      return (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <EmptyVariable />
        </Box>
      );
    }
    if (isDatasetLoading) {
      return (
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            height: 200,
          }}
        >
          <CircularProgress size={24} />
        </Box>
      );
    }

    switch (activeView) {
      case VIEW.MANUAL_FORM:
        return (
          <FormProvider {...methods}>
            <ManualVariablesForm
              formValues={formValues}
              isDirty={isDirty}
              cellMap={cellMap}
              graphId={graphId}
              variables={datasetVariables}
            />
          </FormProvider>
        );
      case VIEW.UPLOADED_JSON:
        return (
          <UploadedJSON
            uploadedJson={uploadedJson}
            uploadedFileName={uploadedFileName}
            setUploadedJson={setUploadedJson}
          />
        );
      default:
        return (
          <FormProvider {...methods}>
            <ManualVariablesForm
              formValues={formValues}
              isDirty={isDirty}
              cellMap={cellMap}
              graphId={graphId}
              variables={datasetVariables}
            />
          </FormProvider>
        );
    }
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      PaperProps={{
        sx: {
          height: "100vh",
          boxShadow: "none !important",
          borderRadius: "0px !important",
          borderLeft: "1px solid",
          borderColor: "divider",
          "&.MuiDrawer-paper": {
            backgroundColor: "background.paper",
            backgroundImage: "none",
          },
          display: "flex",
          overflow: "visible",
          transition:
            "min-width 300ms ease-in-out, max-width 300ms ease-in-out",
          minWidth: "700px",
          padding: 1.5,
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: {
            backgroundColor: "transparent",
            boxShadow: "none",
          },
        },
      }}
    >
      <Header
        // showHeaderActions={activeView !== VIEW.ACTIONS}
        showHeaderActions={false}
        onClose={handleClose}
        handleUploadJson={handleUploadJson}
        currentView={currentView}
        // onOpenImportDatasetDrawer={handleOpenImportDatasetDrawer}
        disabled={variableKeys.length === 0}
      />
      <Divider sx={{ my: 1.5 }} />

      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
        }}
      >
        {renderContent()}
      </Box>

      {/* Upload JSON Dialog */}
      <UploadJsonDialog
        open={showUploadJsonDialog}
        onClose={handleUploadJsonClose}
        onFileSelect={handleFileSelect}
        onSuccessComplete={handleUploadSuccess}
        isLoading={uploadMutation.isPending}
        isSuccess={uploadMutation.isSuccess}
        error={
          uploadMutation.isError
            ? "Failed to upload file. Please ensure it's a valid JSON file."
            : null
        }
      />

      {/* Import Dataset Drawer */}
      {/* <ImportDatasetDrawer
        open={importDatasetDrawerOpen}
        onClose={handleCloseImportDatasetDrawer}
        variables={variableKeys}
        setVariableData={handleSetVariableData}
      /> */}

      {/* Confirm Dialog for unsaved changes */}
      <ConfirmDialog
        open={showConfirmDialog}
        onClose={cancelClose}
        title="Unsaved Changes"
        content="You have unsaved changes. Are you sure you want to close without saving?"
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={confirmClose}
            sx={{ paddingX: "24px" }}
          >
            Discard Changes
          </Button>
        }
      />
    </Drawer>
  );
}

GlobalVariableDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
