import React, { useState } from "react";
import { StaticColumns, DynamicColumns } from "./common";
import {
  Box,
  Button,
  IconButton,
  Modal,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import _ from "lodash";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
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

  // Filter columns based on search query
  const filteredColumns = allColumns.filter((col) =>
    col.label.toLowerCase().includes(searchQuery.toLowerCase()),
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
      setOpenExtractJsonKey(true);
    } else if (type === "retrieval") {
      setOpenRetrieval(true);
    } else if (type === "conditionalnode") {
      setOpenConditionalNode(true);
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
            <Box
              key={value}
              onClick={() => handleColumnClick({ label, value })}
              sx={{
                display: "flex",
                flexDirection: "column",
                padding: "10px 15px",
                cursor: "pointer",
                borderRadius: "8px",
                backgroundColor: "transparent",
                transition: "background-color 0.3s ease",
                "&:hover": { backgroundColor: "action.hover" },
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
                    fontSize: "14px",
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
                  transition: "color 0.3s ease",
                }}
              >
                {helpingText}
              </Typography>
            </Box>
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
  const columnNameError = !columnName.trim()
    ? "Column name is required"
    : columnName.length > 50
      ? "Column name must be less than 50 characters"
      : !/^[a-z0-9_-]+$/.test(columnName)
        ? "Only lowercase letters, numbers, underscores, and hyphens are allowed (no spaces)"
        : "";
  const { openAddColumnDrawer, setOpenAddColumnDrawer } =
    useAddColumnDrawerStore();
  const onClose = () => {
    setOpenAddColumnDrawer(false);
  };

  const { refreshGrid } = useDevelopDetailsContext();

  const { mutate: addColumn, isPending: isLoading } = useMutation({
    /**
     * @param {object} d
     */
    mutationFn: (d) => axios.post(endpoints.develop.addColumn(dataset), d),
    onSuccess: () => {
      enqueueSnackbar("Column added successfully", { variant: "success" });
      refreshGrid();
      setColumnName("");
      setStaticColumnType("");
      onClose();
    },
  });

  const handleClose = () => {
    setStaticColumnType("");
    setColumnName("");
    onClose();
  };

  return (
    <Modal open={openAddColumnDrawer} onClose={handleClose} disableAutoFocus>
      <Box
        sx={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: staticColumnType
            ? "592px"
            : hideScenarioFeatures
              ? "592px"
              : "850px",
          height: staticColumnType ? "205px" : "700px",
          bgcolor: "background.paper",
          borderRadius: "16px",
          boxShadow: 24,
          p: 3,
        }}
      >
        <IconButton
          onClick={handleClose}
          sx={{
            position: "absolute",
            top: "16px",
            right: staticColumnType ? "10px" : "40px",
          }}
        >
          <Iconify icon="mingcute:close-line" />
        </IconButton>
        <Typography fontWeight={700} fontSize={"18px"} color="text.primary">
          {staticColumnType ? `${staticColumnType} Column` : "Add Columns"}
        </Typography>
        {!staticColumnType ? (
          <Box sx={{ marginTop: "25px", marginRight: "10px" }}>
            <FormSearchField
              fullWidth
              size="small"
              placeholder="Search Columns"
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
                      fontWeight: 700,
                    }}
                  >
                    DATA TYPE
                  </Typography>
                  <Box sx={{ marginTop: "20px" }}>
                    {dataType.map((val, index) => (
                      <Box
                        key={index}
                        onClick={() => setSelectedDataType(val.name)}
                        sx={{
                          borderTopLeftRadius: "10px",
                          borderBottomLeftRadius: "10px",
                          backgroundColor:
                            selectedDataType === val.name
                              ? "action.hover"
                              : "background.paper",
                          height: "39px",
                          paddingLeft: "20px",
                          marginBottom: "7px",
                          display: "flex",
                          alignItems: "center",
                          cursor: "pointer",
                        }}
                      >
                        <Typography
                          sx={{
                            fontSize: "14px",
                            fontWeight: 600,
                            color:
                              selectedDataType === val.name
                                ? "primary.main"
                                : "text.secondary",
                          }}
                        >
                          {val.name}
                        </Typography>
                      </Box>
                    ))}
                  </Box>
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
            <TextField
              fullWidth
              size="small"
              label="Column name"
              placeholder="Enter column name"
              value={columnName}
              required
              error={Boolean(columnNameError && columnName)}
              helperText={columnName ? columnNameError : undefined}
              onChange={(e) => setColumnName(e.target.value)}
            />
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
                  paddingX: "16px",
                  borderRadius: "10px",
                  fontSize: "14px",
                  fontWeight: 700,
                  color: "text.primary",
                  border: "1px solid var(--border-default)",
                }}
                onClick={handleClose}
              >
                Cancel
              </Button>
              <LoadingButton
                sx={{
                  paddingX: "16px",
                  borderRadius: "10px",
                  fontSize: "14px",
                  fontWeight: 700,
                }}
                variant="contained"
                color="primary"
                onClick={() => {
                  addColumn({
                    new_column_name: columnName,
                    column_type: staticColumnType
                      .toLowerCase()
                      .replace(/\s+/g, ""),
                  });
                }}
                disabled={!columnName.trim() || Boolean(columnNameError)}
                loading={isLoading}
              >
                Add Column
              </LoadingButton>
            </Box>
          </Box>
        )}
      </Box>
    </Modal>
  );
};

AddColumnDrawer.propTypes = {
  hideScenarioFeatures: PropTypes.bool,
};

export default AddColumnDrawer;
