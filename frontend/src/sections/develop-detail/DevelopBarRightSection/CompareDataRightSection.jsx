import {
  Box,
  Button,
  Divider,
  IconButton,
  Typography,
  Switch,
} from "@mui/material";
import React, { useState, useRef, useEffect } from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import CustomTooltip from "src/components/tooltip";
import ColumnMenu from "./ColumnMenu";
import { useDevelopDetailContext } from "src/pages/dashboard/Develop/Context/DevelopDetailContext";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const CompareDataRightSection = ({
  columns = [],
  setSelectedColumn,
  baseColumn,
  selectedDatasetsValues,
  onRunEvaluation,
  compareId,
  setColumns,
  selectedColumns,
}) => {
  const [columnPopoverOpen, setColumnPopoverOpen] = useState(false);
  // const [selectedColumns, setSelectedColumns] = useState([]);
  const [unselectedColumns] = useState([]);
  const columnButtonRef = useRef(null);
  const { diffMode, handleToggleDiffMode } = useDevelopDetailContext();
  const { role } = useAuthContext();

  // Initialize all columns as selected only on first load, i.e. while nothing is
  // selected yet. React Query hands back a new `columns` array reference on every
  // refetch even when the data is unchanged; the previous `[columns]` dependency
  // fired this effect on those reference changes and reset every checkbox,
  // silently discarding the user's deselections. Guarding on "nothing selected
  // yet" preserves the user's choices while still initializing once the columns
  // finish loading (which happens after mount, so a mount-only `[]` effect would
  // never run).
  useEffect(() => {
    if (selectedColumns.length > 0 || columns.length === 0) {
      return;
    }
    const temp = [];
    for (const col of columns) {
      temp.push(col?.id);
      if (col?.children?.length > 0) {
        for (const ccol of col.children) {
          temp.push(`${ccol?.id}`);
        }
      }
    }
    setSelectedColumn(temp);
  }, [columns, selectedColumns]);

  const handleColumnClick = () => {
    setColumnPopoverOpen(true);
  };

  // This function handles the popover closing for any reason
  const handlePopoverClose = () => {
    setColumnPopoverOpen(false);
  };

  const { mutate: handleDownloadCompareDataset } = useMutation({
    mutationFn: () =>
      axios.post(
        endpoints.dataset.getCompareDatasetDownload(selectedDatasetsValues[0]),
        {
          dataset_ids: selectedDatasetsValues.slice(1),
          base_column_name: baseColumn,
          compare_id: compareId?.current,
        },
      ),
    onSuccess: (response) => {
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `dataset-${selectedDatasetsValues[0]}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      enqueueSnackbar("Dataset downloaded successfully", {
        variant: "success",
      });
    },
  });

  const handleColumnSelect = (column, parentId) => {
    let copy = [...selectedColumns];

    if (!parentId) {
      // Parent node is clicked
      const isParentChecked = copy.includes(column);
      if (isParentChecked) {
        // --- Remove parent and children
        const columnFound = columns.find((col) => col.id === column);
        const idsToRemove = [];

        if (columnFound) {
          columnFound?.children?.forEach((child) => {
            idsToRemove.push(`${child.id}`);
          });
        }
        idsToRemove.push(column);

        copy = copy.filter((id) => !idsToRemove.includes(id));
        // setSelectedColumns(copy);
        setSelectedColumn(copy);
      } else {
        // --- Add parent and all children
        const columnFound = columns.find((col) => col.id === column);
        const idsToAdd = [];

        if (columnFound) {
          columnFound?.children?.forEach((child) => {
            idsToAdd.push(`${child.id}`);
          });
        }
        idsToAdd.push(column);

        // @ts-ignore
        copy = [...new Set([...copy, ...idsToAdd])];
        setSelectedColumn(copy);
      }

      return;
    } else {
      // Child node is clicked
      const isSelected = copy.includes(column);

      let newSelected = [];

      if (isSelected) {
        // --- Deselect child
        newSelected = copy.filter((id) => id !== column);
      } else {
        // --- Select child
        newSelected = [...copy, column];
      }

      // Now handle parent selection logic
      const siblings =
        columns.find((col) => col.id === parentId)?.children || [];

      const allSiblingsSelected = siblings.every((sibling) => {
        const siblingId = `${sibling.id}`;
        return newSelected.includes(siblingId);
      });

      if (allSiblingsSelected) {
        // --- All siblings selected -> select parent
        newSelected.push(parentId);
      } else {
        // --- Some siblings unselected -> unselect parent
        newSelected = newSelected.filter((id) => id !== parentId);
      }

      // Finally update
      // @ts-ignore
      setSelectedColumn([...new Set(newSelected)]);
    }
  };

  return (
    <Box
      display="flex"
      alignItems="center"
      sx={{
        height: "30px",
      }}
    >
      <IconButton
        size="small"
        sx={{
          color: "text.secondary",
          borderRadius: 1,
        }}
      >
        <img
          style={{
            height: "20px",
            width: "20px",
            cursor: "pointer",
          }}
          ref={columnButtonRef}
          onClick={handleColumnClick}
          alt="?"
          src="/icons/datasets/flowbite_column-outline .svg"
        />
      </IconButton>
      <Divider
        orientation="vertical"
        flexItem
        sx={{ mx: "12px", maxHeight: "30px" }}
      />
      <Box sx={{ display: "flex", alignItems: "center" }}>
        <Switch
          size="small"
          color="success"
          checked={diffMode}
          onChange={() => {
            handleToggleDiffMode();
          }}
        />
        <Typography variant="caption" fontWeight={400}>
          Show Diff
        </Typography>
        <CustomTooltip
          show
          title="Shows the difference between the primary dataset vs the selected comparison datasets"
          placement="bottom"
          arrow
        >
          <Iconify
            width={16}
            icon="material-symbols:info-outline-rounded"
            sx={{ color: "text.secondary", marginLeft: 0.5 }}
          />
        </CustomTooltip>
      </Box>

      <Divider
        orientation="vertical"
        flexItem
        sx={{ mx: "12px", maxHeight: "30px" }}
      />
      <Button
        variant="contained"
        color="primary"
        size="small"
        sx={{
          px: "16px",
          py: "6px",
          color: "common.white",
        }}
        startIcon={
          <Iconify
            sx={{
              height: "16px",
              width: "16px",
            }}
            icon="material-symbols:check-circle-outline"
          />
        }
        onClick={() =>
          onRunEvaluation({
            selectedColumns,
            unselectedColumns,
          })
        }
        disabled={!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
      >
        Evaluate
      </Button>

      <Divider
        orientation="vertical"
        flexItem
        sx={{ mx: "12px", maxHeight: "30px" }}
      />

      <IconButton
        size="small"
        sx={{
          color: "text.secondary",
          borderRadius: 1,
          paddingX: 1,
        }}
        onClick={handleDownloadCompareDataset}
      >
        <Iconify icon="material-symbols:download" />
      </IconButton>

      {/* Column Selector Popover */}
      <ColumnMenu
        open={columnPopoverOpen}
        anchorEl={columnButtonRef.current}
        onClose={handlePopoverClose}
        columns={columns ?? []}
        setColumns={setColumns}
        // children={column?.children}
        // headerName={column?.headerName}
        onColumnVisibilityChange={handleColumnSelect}
        selectedColumnIds={selectedColumns}
      />
    </Box>
  );
};

CompareDataRightSection.propTypes = {
  datasetInfo: PropTypes.array,
  commonColumn: PropTypes.array,
  setSelectedColumn: PropTypes.func,
  refreshGrid: PropTypes.func,
  onRunEvaluation: PropTypes.func,
  selectedDatasetsValues: PropTypes.array,
  baseColumn: PropTypes.string,
  columns: PropTypes.arrayOf(PropTypes.string),
  compareId: PropTypes.any,
  setColumns: PropTypes.func,
  selectedColumns: PropTypes.array,
};

export default CompareDataRightSection;

// filteredColumns?.map((column) => (
// <ColumnMenuItem
// key={column.headerName}
// children={column?.children ?? []}
// handleColumnSelect={handleColumnSelect}
// headerName={column.headerName}
// selectedColumns={selectedColumns} />
// ))

// filteredColumns.map((column) => (
//   <ColumnMenu
//     children={column?.children}
//     key={column?.id}
//     id={column?.id}
//     headerName={column?.headerName}
//     handleColumnSelect={handleColumnSelect}
//     selectedColumnIds={selectedColumns}
//   />
// ))

// filteredColumns.map((column) => (
//   <ColumnMenu
//     children={column?.children}
//     key={column?.id}
//     id={column?.id}
//     headerName={column?.headerName}
//     handleColumnSelect={handleColumnSelect}
//     selectedColumnIds={selectedColumns}
//   />
// ))

{
  /* <ColumnMenuList 
data={filteredColumns} 
handleColumnSelect={handleColumnSelect} 
selectedColumns={selectedColumns} 
moveColumn={moveColumn} /> */
}
