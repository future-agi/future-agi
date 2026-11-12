import { useState } from "react";
import { StaticColumns, DynamicColumns } from "./common";
import ExpressionColumnDialog from "./Expression/ExpressionColumnDialog";
import {
  EXPRESSION_COLUMN_TYPE,
  useExpressionColumnStore,
} from "./Expression/expressionColumnModel";
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  FormControlLabel,
  Radio,
  RadioGroup,
  IconButton,
  MenuItem,
  Modal,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { getInlineActionErrorMessage } from "src/utils/errorUtils";
import { useParams } from "react-router";
import { useColumnFromPathStore } from "src/sections/common/Values/columnFromPathStore";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import {
  inferRole,
  ROLE_LABEL,
  USER_ROLES,
} from "../DataTab/DataGrid/dataGridView";
import {
  useConditionalNodeStoreShallow,
  useRetrievalStoreShallow,
  useExtractJsonKeyStoreShallow,
  useClassificationStoreShallow,
  useExtractEntitiesStoreShallow,
  useExecuteCodeStoreShallow,
  useAddColumnApiCallStoreShallow,
  useRunPromptStoreShallow,
  useAddColumnDrawerStore,
  useAddColumnDrawerStoreShallow,
} from "../states";
import { useDevelopDetailContext } from "src/pages/dashboard/Develop/Context/DevelopDetailContext";
import { useDevelopDetailContext as useDevelopDetailsContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { useCreatedColumnsStore } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { runColumnMutation } from "../DataTab/columnMutationRequest";

const dataType = [
  { name: "All" },
  { name: "Static Columns" },
  { name: "Dynamic Columns" },
];

const AddColumnForm = ({
  selectedDataType,
  searchQuery,
  setStaticColumnType,
  hideScenarioFeatures,
}) => {
  const setOpenRunPrompt = useRunPromptStoreShallow((s) => s.setOpenRunPrompt);
  const { setActionSource } = useDevelopDetailContext();
  const { dataset: datasetId } = useParams();
  const setOpenAddColumnApiCall = useAddColumnApiCallStoreShallow(
    (s) => s.setOpenAddColumnApiCall,
  );
  const setOpenExecuteCode = useExecuteCodeStoreShallow(
    (s) => s.setOpenExecuteCode,
  );
  const setOpenExtractEntities = useExtractEntitiesStoreShallow(
    (s) => s.setOpenExtractEntities,
  );
  const setOpenClassification = useClassificationStoreShallow(
    (s) => s.setOpenClassification,
  );
  const setOpenExtractJsonKey = useExtractJsonKeyStoreShallow(
    (s) => s.setOpenExtractJsonKey,
  );
  const setOpenRetrieval = useRetrievalStoreShallow((s) => s.setOpenRetrieval);
  const setOpenConditionalNode = useConditionalNodeStoreShallow(
    (s) => s.setOpenConditionalNode,
  );
  const setOpenAddColumnDrawer = useAddColumnDrawerStoreShallow(
    (s) => s.setOpenAddColumnDrawer,
  );
  // Determine which columns to display based on selectedDataType
  const allColumns = hideScenarioFeatures
    ? StaticColumns // Only show static columns in scenario context
    : selectedDataType === "Static Columns"
      ? StaticColumns
      : selectedDataType === "Dynamic Columns"
        ? DynamicColumns
        : [...StaticColumns, ...DynamicColumns];

  // IT-R2 G5 P5: search reads the label, the description and synonyms
  // (`path`, `nested`, `json` find Extract a JSON key).
  const q = searchQuery.trim().toLowerCase();
  // A search looks across Static and Dynamic (the tab is a browse aid).
  const pool =
    q && !hideScenarioFeatures
      ? [...StaticColumns, ...DynamicColumns]
      : allColumns;
  const filteredColumns = pool.filter(
    (col) =>
      !q ||
      [col.label, col.helpingText, ...(col.keywords ?? [])].some((t) =>
        String(t ?? "")
          .toLowerCase()
          .includes(q),
      ),
  );
  // const onSubmit = (data) => {
  //   trackEvent(Events.manualDatasetColumnAdditionSuccessful, {
  //     [PropertyName.type]: data?.columnType,
  //     [PropertyName.columnName]: data?.newColumnName,
  //   });
  //   //@ts-ignore
  //   addColumn({
  //     new_column_name: data.newColumnName,
  //     column_type: data.columnType,
  //   });
  //   trackEvent(Events.addColumnsSuccess, {
  //     [PropertyName.newColumn]:{
  //       newColumnName : data.newColumnName,
  //       columnType : data.columnType ,
  //     },
  //   });
  // };

  const openDynamicColumnTypeDrawer = (type) => {
    setOpenAddColumnDrawer(false);
    if (type === "run_prompt") {
      setActionSource("dynamic_column_run_prompt");
      setOpenRunPrompt(true);
    } else if (type === "apiCall") {
      setOpenAddColumnApiCall(true);
    } else if (type === "executeCustomCode") {
      setOpenExecuteCode(true);
    } else if (type === "extractEntities") {
      setOpenExtractEntities(true);
    } else if (type === "classification") {
      setOpenClassification(true);
    } else if (type === "extractJsonKey") {
      // IT-R2 G5 P5: the one path picker (sample values + coverage); the
      // legacy free-text form stays only where no dataset is open.
      if (datasetId) useColumnFromPathStore.getState().openFor({ datasetId });
      else setOpenExtractJsonKey(true);
    } else if (type === "retrieval") {
      setOpenRetrieval(true);
    } else if (type === "conditionalnode") {
      setOpenConditionalNode(true);
    } else if (type === EXPRESSION_COLUMN_TYPE && datasetId) {
      useExpressionColumnStore.getState().openFor({ datasetId });
    }
  };

  // Function to handle column selection
  const handleColumnClick = (columnData) => {
    // Check if the selected column is from DynamicColumns or StaticColumns
    if (DynamicColumns.some((column) => column.value === columnData.value)) {
      openDynamicColumnTypeDrawer(columnData.value);
    } else {
      setStaticColumnType(columnData.label);
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "16px",
        height: "100%",
        width: "100%",
      }}
    >
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: "10px",
          overflowY: "auto",
          maxHeight: "550px",
        }}
      >
        {filteredColumns.length > 0 ? (
          filteredColumns.map(({ label, value, icon, helpingText }) => (
            <ButtonBase
              key={value}
              aria-label={label}
              onClick={() => handleColumnClick({ label, value })}
              sx={{
                display: "flex",
                flexDirection: "column",
                padding: "10px 15px",
                cursor: "pointer",
                borderRadius: "8px",
                backgroundColor: "transparent",
                transition: "background-color 0.2s ease",
                alignItems: "stretch",
                textAlign: "left",
                flexShrink: 0,
                "&:hover": { backgroundColor: "action.hover" },
                "&.Mui-focusVisible": {
                  outline: "2px solid var(--ds-border-focus)",
                  outlineOffset: -2,
                },
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  marginBottom: "5px",
                }}
              >
                <Iconify icon={icon} width={20} height={20} />
                <Typography
                  sx={{
                    fontSize: "13px",
                    fontWeight: 600,
                    color: "text.primary",
                  }}
                >
                  {label}
                </Typography>
              </Box>
              <Typography
                sx={{
                  fontSize: "12px",
                  color: "text.secondary",
                  marginLeft: "28px",
                  transition: "color 0.2s ease",
                }}
              >
                {helpingText}
              </Typography>
            </ButtonBase>
          ))
        ) : (
          <Typography
            sx={{
              textAlign: "center",
              color: "text.secondary",
              marginTop: "20px",
            }}
          >
            No columns found
          </Typography>
        )}
      </Box>
    </Box>
  );
};

AddColumnForm.propTypes = {
  openDynamicColumnTypeDrawer: PropTypes.func,
  setStaticColumnType: PropTypes.func.isRequired,
  selectedDataType: PropTypes.string.isRequired,
  searchQuery: PropTypes.string.isRequired,
  hideScenarioFeatures: PropTypes.bool,
};

const AddColumnDrawer = ({ hideScenarioFeatures = false }) => {
  const { dataset } = useParams();
  const [selectedDataType, setSelectedDataType] = useState("All");
  const [searchQuery, setSearchQuery] = useState("");
  const [staticColumnType, setStaticColumnType] = useState("");
  const [columnName, setColumnName] = useState("");
  // G3: user columns carry a role; inferred from the name until picked.
  const [columnRole, setColumnRole] = useState(null);
  const effectiveRole = columnRole ?? inferRole(columnName);
  const { openAddColumnDrawer, setOpenAddColumnDrawer } =
    useAddColumnDrawerStore();
  const onClose = () => {
    setOpenAddColumnDrawer(false);
  };

  const { refreshGrid } = useDevelopDetailsContext();

  const {
    mutate: addColumn,
    isPending: isLoading,
    error,
    reset: resetMutation,
  } = useMutation({
    networkMode: "always",
    meta: { errorHandled: true },
    /**
     * @param {object} d
     */
    mutationFn: (d) =>
      runColumnMutation((config) =>
        axios.post(endpoints.develop.addColumn(dataset), d, config),
      ),
    onSuccess: (res, d) => {
      // integration (DV-01): a column is written at once and is not versioned;
      // say so. Only while changes are pending is it recorded with them
      // (the store ignores it otherwise), so Discard can offer to delete it.
      const id = res?.data?.result?.column_id;
      const name = d?.new_column_name ?? columnName;
      if (id)
        useCreatedColumnsStore
          .getState()
          .add(dataset, { id, name, from: "add_column" });
      enqueueSnackbar(
        `Column “${name}” added`,
        { variant: "success" },
      );
      refreshGrid();
      setColumnName("");
      setColumnRole(null);
      setStaticColumnType("");
      onClose();
    },
  });
  // A refusal the workspace recovery handles shows one calm line.
  const errorMessage = error
    ? getInlineActionErrorMessage(error, "Couldn't save the column")
    : null;

  const handleClose = () => {
    if (isLoading) return;
    resetMutation();
    setStaticColumnType("");
    setColumnName("");
    setColumnRole(null);
    onClose();
  };

  // HO D7: the Expression column dialog outlives this menu.
  return [
    <ExpressionColumnDialog key="expression-column" />,
    <Modal key="add-column" open={openAddColumnDrawer} onClose={handleClose}>
      <Box
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-column-title"
        tabIndex={-1}
        sx={{
          maxWidth: "calc(100vw - 32px)",
          maxHeight: "calc(100vh - 32px)",
          overflowY: "auto",
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: staticColumnType
            ? "592px"
            : hideScenarioFeatures
              ? "592px"
              : "850px",
          height: staticColumnType ? "auto" : "700px",
          bgcolor: "background.paper",
          borderRadius: "12px",
          boxShadow: (theme) => theme.customShadows?.dialog,
          p: 3,
        }}
      >
        <IconButton
          aria-label="Close add columns"
          disabled={isLoading}
          onClick={handleClose}
          sx={{
            position: "absolute",
            top: "16px",
            right: staticColumnType ? "10px" : "40px",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
        <Typography
          id="add-column-title"
          fontWeight={600}
          fontSize={"16px"}
          color="text.primary"
        >
          {staticColumnType ? `${staticColumnType} Column` : "Add Columns"}
        </Typography>
        {!staticColumnType ? (
          <Box sx={{ marginTop: "25px", marginRight: "10px" }}>
            <FormSearchField
              fullWidth
              size="small"
              placeholder="Search Columns"
              inputProps={{ "aria-label": "Search column types" }}
              searchQuery={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <Box sx={{ display: "flex", height: "100%", marginTop: "30px" }}>
              {!hideScenarioFeatures && (
                <Box sx={{ width: "30%", height: "550px" }}>
                  <Typography
                    sx={{
                      color: "text.secondary",
                      fontSize: "12px",
                      paddingLeft: "13px",
                      fontWeight: 600,
                    }}
                  >
                    Data type
                  </Typography>
                  <RadioGroup
                    aria-label="Column category"
                    value={selectedDataType}
                    onChange={(event) =>
                      setSelectedDataType(event.target.value)
                    }
                    sx={{ mt: "20px" }}
                  >
                    {dataType.map(({ name }) => (
                      <FormControlLabel
                        key={name}
                        value={name}
                        control={<Radio size="small" />}
                        label={name}
                        sx={{
                          m: 0,
                          mb: 1,
                          borderRadius: "var(--ds-radius-lg)",
                          bgcolor:
                            selectedDataType === name
                              ? "var(--ds-bg-selected)"
                              : "transparent",
                          color:
                            selectedDataType === name
                              ? "var(--ds-fg-default)"
                              : "text.secondary",
                          "& .MuiFormControlLabel-label": {
                            fontSize: 13,
                            fontWeight: 600,
                          },
                        }}
                      />
                    ))}
                  </RadioGroup>
                </Box>
              )}
              <Box
                sx={{
                  flex: 1,
                  minWidth: 0,
                  marginLeft: hideScenarioFeatures ? "0" : "65px",
                }}
              >
                <AddColumnForm
                  selectedDataType={selectedDataType}
                  searchQuery={searchQuery}
                  setStaticColumnType={setStaticColumnType}
                  hideScenarioFeatures={hideScenarioFeatures}
                />
              </Box>
            </Box>
          </Box>
        ) : (
          <Box sx={{ marginTop: "25px", marginRight: "30px" }}>
            {errorMessage && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {errorMessage}
              </Alert>
            )}
            <Box sx={{ display: "flex", gap: 1.5 }}>
              <TextField
                fullWidth
                size="small"
                label="Column name"
                autoFocus
                placeholder="Enter column name"
                value={columnName}
                disabled={isLoading}
                required
                onChange={(e) => setColumnName(e.target.value)}
              />
              <TextField
                select
                size="small"
                label="Role"
                value={effectiveRole}
                disabled={isLoading}
                onChange={(e) => setColumnRole(e.target.value)}
                sx={{ width: 200, flexShrink: 0 }}
                helperText={columnRole ? " " : "From the column name"}
              >
                {[...USER_ROLES, "none"].map((role) => (
                  <MenuItem key={role} value={role}>
                    {role === "none" ? "No role" : ROLE_LABEL[role]}
                  </MenuItem>
                ))}
              </TextField>
            </Box>
            <Box
              sx={{
                display: "flex",
                flexDirection: "row",
                justifyContent: "flex-end",
                marginTop: "25px",
                gap: 1.5,
                marginRight: "-30px",
              }}
            >
              <Button
                sx={{
                  color: "text.primary",
                  border: "1px solid var(--border-default)",
                }}
                onClick={handleClose}
                disabled={isLoading}
              >
                Cancel
              </Button>
              <LoadingButton
                variant="contained"
                color="primary"
                onClick={() => {
                  addColumn({
                    new_column_name: columnName,
                    column_type: staticColumnType
                      .toLowerCase()
                      .replace(/\s+/g, ""),
                    role: effectiveRole,
                  });
                }}
                disabled={!columnName}
                loading={isLoading}
              >
                Add Column
              </LoadingButton>
            </Box>
          </Box>
        )}
      </Box>
    </Modal>,
  ];
};

AddColumnDrawer.propTypes = {
  hideScenarioFeatures: PropTypes.bool,
};

export default AddColumnDrawer;
