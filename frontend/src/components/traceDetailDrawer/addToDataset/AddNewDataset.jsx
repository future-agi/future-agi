import React, { useState, useEffect } from "react";
import {
  Typography,
  Box,
  Grid,
  TextField,
  Checkbox,
  Button,
  useTheme,
} from "@mui/material";
import Iconify from "../../iconify";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { useNavigate, useParams } from "react-router";
import { LLM_TABS } from "../../../sections/projects/LLMTracing/common";
import { coreSpanFields } from "../common";

const AddNewDataset = ({
  handleclose,
  selectedNode,
  observationFields,
  selectedTraces,
  selectedSpans,
  selectAll,
  currentTab,
  onSuccess,
}) => {
  const [mapToNewDatasetColumn, setMapToNewDatasetColumn] = useState([]);
  const [dataset, setDataset] = useState("");
  const theme = useTheme();
  const navigate = useNavigate();
  const { observeId } = useParams();

  const generateNewDatasetMapping = (observationFields) => {
    const newMappedColumns = observationFields.map((field) => ({
      name: field.name,
      column: field.name,
      checked: true,
      type: field.type,
    }));

    // Sort: checked fields first
    newMappedColumns.sort((a, b) => {
      if (a.checked === b.checked) return 0;
      return a.checked ? -1 : 1;
    });

    setMapToNewDatasetColumn(newMappedColumns);
  };

  useEffect(() => {
    if (observationFields && observationFields.length > 0) {
      generateNewDatasetMapping(observationFields);
    }
  }, [observationFields]);

  const handleCheckboxChange = (index) => {
    const updated = [...mapToNewDatasetColumn];
    updated[index].checked = !updated[index].checked;
    updated.sort((a, b) => {
      if (a.checked === b.checked) return 0;
      return a.checked ? -1 : 1;
    });
    setMapToNewDatasetColumn(updated);
  };

  const handleColumnNameChange = (index, value) => {
    const updated = [...mapToNewDatasetColumn];
    updated[index].column = value;

    setMapToNewDatasetColumn(updated);
  };

  const hasDuplicateColumnNames = () => {
    const names = mapToNewDatasetColumn
      .filter((f) => f.checked && f.column.trim() !== "")
      .map((f) => f.column.trim().toLowerCase());

    const unique = new Set(names);
    return unique.size !== names.length;
  };

  const isDuplicateColumnName = (value, index) => {
    if (!value.trim()) return false;

    return mapToNewDatasetColumn.some(
      (item, idx) =>
        idx !== index &&
        item.checked &&
        item.column.trim().toLowerCase() === value.trim().toLowerCase(),
    );
  };

  const isSubmitDisabled =
    !dataset ||
    mapToNewDatasetColumn.some((f) => f.checked && f.column.trim() === "") ||
    !mapToNewDatasetColumn.some((f) => f.checked && f.column.trim() !== "") ||
    hasDuplicateColumnNames();

  const { mutate: addToDatasetMutation, isPending: addingToDataset } =
    useMutation({
      /**
       *
       * @param {Object} payload
       * @returns
       */
      mutationFn: (payload) =>
        axios.post(endpoints.project.addNewDataset, payload),

      onSuccess: (res) => {
        enqueueSnackbar(
          <>
            Datapoints added to newly created dataset
            <span
              onClick={() =>
                navigate(
                  `/dashboard/develop/${res?.data?.result?.dataset_id ?? res?.data?.result?.datasetId}?tab=data`,
                  { state: { from: location.pathname } },
                )
              }
              style={{
                textDecoration: "underline",
                cursor: "pointer",
                color: theme.palette.green[500],
                fontWeight: "500",
                marginLeft: "10px",
              }}
            >
              View Dataset
            </span>
          </>,
          { variant: "success" },
        );
        handleclose();
        if (onSuccess) {
          onSuccess();
        }
      },
    });
  const handleSubmit = () => {
    const mapping_config = mapToNewDatasetColumn
      .filter(({ checked, column }) => checked && column.trim() !== "")
      .map(({ column, name, type }) => ({
        col_name: column,
        data_type: type || "text",
        span_field: name,
      }));

    addToDatasetMutation({
      new_dataset_name: dataset,
      model_type: "BinaryClassification",
      mapping_config,
      select_all: Boolean(selectAll),
      project: observeId,
      ...(currentTab === LLM_TABS.TRACE
        ? {
            trace_ids: selectedTraces,
          }
        : {
            span_ids: selectedNode ? [selectedNode] : selectedSpans,
          }),
    });
  };

  return (
    <>
      <TextField
        fullWidth
        size="small"
        label="Dataset Name"
        placeholder="Enter new dataset name"
        value={dataset}
        onChange={(e) => setDataset(e.target.value)}
      />

      <Typography
        variant="body2"
        fontWeight={500}
        mt={2}
        mb={1}
        color={theme.palette.text.primary}
      >
        Map to column
      </Typography>

      <Box
        sx={{
          border: `1px solid ${theme.palette.divider}`,
          borderRadius: "8px",
          px: 1,
          py: 2,
          height: "100%",
          overflowY: "auto",
        }}
      >
        {mapToNewDatasetColumn.length > 0 ? (
          mapToNewDatasetColumn.map((item, index) => (
            <Grid
              container
              alignItems="center"
              mb={2}
              key={index}
              wrap="wrap"
              spacing={1}
            >
              <Grid item xs={12} sm={5}>
                <Box display="flex" alignItems="center">
                  <Checkbox
                    onChange={() => handleCheckboxChange(index)}
                    checked={item.checked}
                    sx={{ marginRight: "5px" }}
                    icon={
                      <Iconify
                        icon="system-uicons:checkbox-empty"
                        color="text.disabled"
                        width="20px"
                        height="20px"
                      />
                    }
                    checkedIcon={
                      <Iconify
                        icon="famicons:checkbox"
                        color="primary.light"
                        width="20px"
                        height="20px"
                      />
                    }
                  />
                  <TextField
                    fullWidth
                    value={item.name}
                    size="small"
                    disabled
                  />
                </Box>
              </Grid>
              {/* Arrow */}
              <Grid item xs={12} sm={2} sx={{ textAlign: "center" }}>
                <Box display="flex" alignItems="center" sx={{ px: 2 }}>
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      bgcolor: theme.palette.divider,
                      borderRadius: "50%",
                    }}
                  />
                  <Box
                    sx={{
                      flexGrow: 1,
                      height: "1px",
                      bgcolor: theme.palette.divider,
                      position: "relative",
                    }}
                  >
                    <Box
                      sx={{
                        position: "absolute",
                        right: 0,
                        top: "50%",
                        transform: "translateY(-50%)",
                        width: 0,
                        height: 0,
                        borderTop: "4px solid transparent",
                        borderBottom: "4px solid transparent",
                        borderLeft: `6px solid ${theme.palette.divider}`,
                      }}
                    />
                  </Box>
                </Box>
              </Grid>
              <Grid item xs={12} sm={5}>
                <TextField
                  key={index}
                  fullWidth
                  size="small"
                  label="Column Name"
                  placeholder="Enter new column name"
                  value={item.column}
                  onChange={(e) =>
                    handleColumnNameChange(index, e.target.value)
                  }
                  error={
                    item.checked && isDuplicateColumnName(item.column, index)
                  }
                  helperText={
                    item.checked && isDuplicateColumnName(item.column, index)
                      ? "Column name already exists"
                      : ""
                  }
                />
              </Grid>
            </Grid>
          ))
        ) : (
          <Box
            sx={{
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Typography variant="body2" color={theme.palette.text.secondary}>
              There is no dataset to perform column mapping
            </Typography>
          </Box>
        )}
      </Box>

      {/* Footer Buttons */}
      <Box mt={2} display="flex" gap={2}>
        <Button
          variant="outlined"
          fullWidth
          sx={{
            fontSize: "14px",
            fontWeight: 500,
            color: theme.palette.text.secondary,
            borderColor: theme.palette.divider,
          }}
          onClick={handleclose}
        >
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          fullWidth
          color="primary"
          loading={addingToDataset}
          disabled={isSubmitDisabled}
          onClick={handleSubmit}
          sx={{
            fontSize: "14px",
            fontWeight: 600,
            "&.Mui-disabled": {
              bgcolor: theme.palette.divider,
              color: theme.palette.background.paper,
            },
          }}
        >
          Add to dataset
        </LoadingButton>
      </Box>
    </>
  );
};

AddNewDataset.propTypes = {
  handleclose: PropTypes.func,
  selectedNode: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  observationFields: PropTypes.array,
  selectedTraces: PropTypes.array,
  selectedSpans: PropTypes.array,
  selectAll: PropTypes.bool,
  currentTab: PropTypes.string,
  onSuccess: PropTypes.func,
};

export default AddNewDataset;
