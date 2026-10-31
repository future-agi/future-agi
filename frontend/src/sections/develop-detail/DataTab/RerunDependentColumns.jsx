import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  Typography,
} from "@mui/material";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import axios, { endpoints } from "src/utils/axios";
import { useRerunDependentColumnsStore } from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import {
  rerunDependentColumnsInOrder,
  toggleDependentColumnSelection,
} from "./dependentColumns";

const RerunDependentColumns = ({ dataset: datasetId }) => {
  const { dataset: urlDataset } = useParams();
  const dataset = urlDataset ?? datasetId;
  const { refreshGrid } = useDevelopDetailContext();
  const { rerunDependentColumns, setRerunDependentColumns } =
    useRerunDependentColumnsStore();
  const [selectedIds, setSelectedIds] = useState([]);

  useEffect(() => {
    setSelectedIds(
      rerunDependentColumns?.dependents?.map((column) => column.id) ?? [],
    );
  }, [rerunDependentColumns]);

  const onClose = () => {
    setRerunDependentColumns(null);
  };

  const toggleColumn = (columnId) => {
    setSelectedIds((current) =>
      toggleDependentColumnSelection({
        columnId,
        selectedIds: current,
        dependentColumns: rerunDependentColumns.dependents,
      }),
    );
  };

  // Only column_config (with each column's status) is read, so fetch a
  // single row instead of the grid's full first page.
  const fetchColumns = async () => {
    const queryOptions = getDatasetQueryOptions(dataset, 0, [], [], "", {
      pageSize: 1,
    });
    const response = await queryOptions.queryFn();
    return response?.data?.result?.column_config ?? [];
  };

  const onConfirm = () => {
    const { sourceColumn, dependents } = rerunDependentColumns;
    const selectedDependents = dependents.filter((column) =>
      selectedIds.includes(column.id),
    );

    // Close at once: the chain lasts as long as its slowest column, so it
    // runs in the background and reports progress through snackbars.
    setRerunDependentColumns(null);
    if (!selectedDependents.length) return;

    enqueueSnackbar(
      `Selected columns will rerun after ${sourceColumn.name || "the updated column"} finishes`,
      { variant: "info" },
    );
    rerunDependentColumnsInOrder({
      sourceColumnId: sourceColumn.id,
      dependentColumns: selectedDependents,
      fetchColumns,
      rerunColumn: async (column) => {
        await axios.post(
          endpoints.develop.addColumns.updateDynamicColumn(column.id),
          { operation_type: column.operationType },
        );
        enqueueSnackbar(`Rerunning ${column.name}`, { variant: "info" });
        // Let the grid show the column as running and poll it.
        refreshGrid();
      },
    })
      .then(() => {
        enqueueSnackbar("Dependent columns rerun successfully", {
          variant: "success",
        });
        refreshGrid();
      })
      .catch((error) => {
        enqueueSnackbar(
          error?.response?.data?.message ||
            error?.message ||
            "Failed to rerun dependent columns",
          { variant: "error" },
        );
      });
  };

  return (
    <Dialog
      open={Boolean(rerunDependentColumns)}
      onClose={onClose}
      aria-labelledby="rerun-dependent-columns-title"
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle
        id="rerun-dependent-columns-title"
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: 2,
        }}
      >
        <Typography component="span" variant="h6">
          Rerun dependent columns?
        </Typography>
        <IconButton aria-label="Close" onClick={onClose}>
          <Iconify icon="mdi:close" />
        </IconButton>
      </DialogTitle>
      <DialogContent sx={{ padding: 2 }}>
        <Typography color="text.secondary" sx={{ mb: 2 }}>
          These columns depend on{" "}
          <Box component="span" sx={{ fontWeight: 600 }}>
            {rerunDependentColumns?.sourceColumn?.name || "the updated column"}
          </Box>
          . Select the columns to rerun after it finishes.
        </Typography>
        <Box sx={{ display: "flex", flexDirection: "column" }}>
          {rerunDependentColumns?.dependents?.map((column) => (
            <FormControlLabel
              key={column.id}
              control={
                <Checkbox
                  checked={selectedIds.includes(column.id)}
                  onChange={() => toggleColumn(column.id)}
                />
              }
              label={column.name}
            />
          ))}
        </Box>
      </DialogContent>
      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onClose} variant="outlined" size="small">
          Skip
        </Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          size="small"
          disabled={!selectedIds.length}
        >
          Rerun selected
        </Button>
      </DialogActions>
    </Dialog>
  );
};

RerunDependentColumns.propTypes = {
  dataset: PropTypes.string,
};

export default RerunDependentColumns;
