import React, { useEffect, useState, useRef } from "react";
import {
  Box,
  Typography,
  Button,
  IconButton,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { LoadingButton } from "@mui/lab";
import { useForm } from "react-hook-form";
import { enqueueSnackbar } from "notistack";
import { menuIcons } from "src/utils/MenuIconSet/svgIcons";
import {
  reorderMenuList,
  setMenuIcons,
} from "src/utils/MenuIconSet/setMeniIcons";
import { ConfirmDialog } from "src/components/custom-dialog";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { APP_CONSTANTS } from "src/utils/constants";

const DEFAULT_COL_DEF = {
  width: 250,
  minWidth: 200,
  maxWidth: 400,
  resizable: true,
};

const menuOrder = [
  "Pin Column",
  "Sort Ascending",
  "Sort Descending",
  "separator",
  "Choose Column",
  "Autosize This Column",
  "Autosize All Column",
  "Reset Column",
  "separator",
  "Delete Column",
];

const VARIABLES_TABLE_THEME_PARAMS = {
  columnBorder: true,
  checkboxBorderRadius: 2,
  checkboxBorderWidth: 1.5,
  rowBorder: true,
  headerFontSize: "13px",
  borderColor: "#E6E6E6",
  headerColumnBorder: { width: "1px" },
  headerRowBorder: { width: "1px" },
  wrapperBorderRadius: 1,
};

const VariablesTable = ({
  variableNames,
  setExtractedVars,
  appliedVariableData,
  setAppliedVariableData,
  gridRef,
  onSelectionChanged,
  selected,
  openDelete,
  setOpenDelete,
  onClose,
  generatedData,
  handleLabelsAdd,
  setOpenReload,
  isPending,
}) => {
  const agTheme = useAgThemeWith(VARIABLES_TABLE_THEME_PARAMS);
  const getMainMenuItems = (params) => {
    const allMenuItems = setMenuIcons(params);
    const menuItems = allMenuItems.slice(0);
    const column = params.column.colDef.field;
    const extraMenuItems = [];

    extraMenuItems.push({
      name: "Delete Column",
      action: () => {
        setIsDelete(column);
      },
      icon: menuIcons["Delete Column"],
    });
    const mainMenuItems = [...extraMenuItems, ...menuItems];
    // return mainMenuItems;
    return reorderMenuList(mainMenuItems, menuOrder);
  };

  const [isDelete, setIsDelete] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);
  const originalValueRef = useRef(null);

  const { control, handleSubmit, reset } = useForm({
    defaultValues: {
      name: "",
    },
  });
  const [openNewVariableModal, setOpenNewVariableModal] = useState(false);
  const [columns, setColumns] = useState(
    variableNames.map((name) => {
      return {
        field: name,
        headerName: name?.toUpperCase(),
        valueGetter: (p) => p.data?.[name],
        flex: 1,
        enableCellChangeFlash: true,
        minWidth: 200,
        maxWidth: 400,
        editable: true,
        autoHeight: true,
        wrapText: true,
        mainMenuItems: getMainMenuItems,
      };
    }),
  );
  const values = Object.values(appliedVariableData ?? {});
  const keys = Object.keys(appliedVariableData ?? {});
  const handleClose = () => {
    reset();
    setOpenNewVariableModal(false);
  };

  const [rows, setRows] = useState(() => {
    const row = [];
    const totalInputs = values[0];
    for (let i = 0; i < totalInputs?.length; i++) {
      const entry = {};
      for (let j = 0; j < values.length; j++) {
        entry[keys[j]] = values[j][i];
      }
      row.push(entry);
    }
    if (row.length === 0) {
      const entry = {};
      for (let j = 0; j < values.length; j++) {
        entry[keys[j]] = "";
      }
      row.push(entry);
    }
    return row.map((item, index) => ({ ...item, id: index }));
  });

  useEffect(() => {
    if (isDelete) {
      let index = undefined;
      setColumns((prev) => {
        const columns = [...prev];
        const newcolumns = [];
        columns.forEach((item, ind) => {
          if (item?.field === isDelete) {
            index = ind;
          } else {
            newcolumns.push(item);
          }
        });
        return newcolumns;
        // return columns.filter((item) => );
      });
      setRows((data) => {
        const newData = data.filter((_, i) => i !== index);
        return newData;
      });
      // setIsDelete(null);
      return;
    }
    if (!(selected?.length && openDelete)) {
      setColumns(
        variableNames.map((name) => {
          return {
            field: name,
            headerName: name?.toUpperCase(),
            valueGetter: (p) => p.data?.[name],
            flex: 1,
            editable: true,
            enableCellChangeFlash: true,
            autoHeight: true,
            minWidth: 200,
            maxWidth: 400,
            wrapText: true,
            mainMenuItems: getMainMenuItems,
          };
        }),
      );
    }
    setRows((data) => {
      if (selected?.length && openDelete) {
        setTimeout(() => {
          onSelectionChanged(null);
          setOpenDelete(false);
        }, 300);
        const selectedSet = new Set(selected.map((temp) => temp.id));
        const newData = data.filter((_, i) => !selectedSet.has(i));
        return newData;
      }
      const row = [];
      const totalInputs = values[0];
      for (let i = 0; i < totalInputs?.length; i++) {
        const entry = {};
        for (let j = 0; j < values.length; j++) {
          entry[keys[j]] = values[j][i];
        }
        row.push(entry);
      }
      if (row.length === 0) {
        const entry = {};
        for (let j = 0; j < values.length; j++) {
          entry[keys[j]] = "";
        }
        row.push(entry);
      }
      return row.map((item, index) => ({ ...item, id: index }));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variableNames, appliedVariableData]);

  useEffect(() => {
    if (generatedData?.length > 0) {
      setRows(generatedData);
    }
  }, [generatedData]);

  const handleAddRow = () => {
    const entry = {};
    for (let j = 0; j < values.length; j++) {
      entry[keys[j]] = "";
    }
    setRows((prev) => {
      const rows = [...prev, entry];
      return rows.map((item, index) => ({ ...item, id: index }));
    });
    setHasChanges(true);
    gridRef.current.api.stopEditing();
  };

  const handleAddVariable = (name) => {
    // setRows((prev)=>[...prev,{}])
    setColumns((prev) => [
      ...prev,
      {
        field: name,
        headerName: name?.toUpperCase(),
        valueGetter: (p) => p.data?.[name],
        flex: 1,
        editable: true,
        minWidth: 200,
        maxWidth: 400,
        mainMenuItems: getMainMenuItems,
      },
    ]);
    // handleAddRow();
    reset();
    setOpenNewVariableModal(false);
  };

  const handleDelete = () => {
    setExtractedVars((pre) => pre.filter((item) => item !== isDelete));
    setIsDelete(null);
  };

  const handleSave = () => {
    setIsSaving(true);

    const rowData = [];
    gridRef.current.api.stopEditing();
    gridRef.current.api.forEachNode((node) => rowData.push(node.data));
    const colsArray = columns.map((column) => column.field);
    const modifiedRowData = {};
    for (let i = 0; i < colsArray.length; i++) {
      modifiedRowData[colsArray[i]] = [];
    }
    for (let i = 0; i < colsArray.length; i++) {
      for (let j = 0; j < rowData.length; j++) {
        modifiedRowData[colsArray[i]].push(rowData[j][colsArray[i]] || "");
      }
    }

    // Simulate async operation and then stop loading
    setTimeout(() => {
      if (appliedVariableData != modifiedRowData) {
        handleLabelsAdd(null);
      }
      setAppliedVariableData(modifiedRowData);
      setIsSaving(false);
      setHasChanges(false);
      enqueueSnackbar("Variables saved successfully", { variant: "success" });
      // Don't close automatically - let user close manually
    }, 500); // Small delay to show loading state
  };

  const onSubmitCreateColumn = (data) => {
    if (!data.name) {
      enqueueSnackbar("Please enter a name", { variant: "error" });
      return;
    }
    handleAddVariable(data.name);
    gridRef.current.api.stopEditing();
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100% - 120px)",
        flex: 1,
      }}
    >
      <ConfirmDialog
        open={Boolean(isDelete)}
        onClose={() => setIsDelete(false)}
        title="Confirm action"
        content="Are you sure want to delete the selected column?"
        action={
          <LoadingButton
            variant="contained"
            color="error"
            onClick={handleDelete}
            loading={false}
          >
            Delete
          </LoadingButton>
        }
      />
      <Box
        sx={{
          padding: "10px 16px 0 16px",
          flex: 1,
          overflow: "auto",
          flexGrow: 1,
          marginBottom: "20px",
        }}
      >
        <AgGridReact
          ref={gridRef}
          rowHeight={359}
          domLayout={"autoHeight"}
          defaultColDef={DEFAULT_COL_DEF}
          onColumnHeaderClicked={(event) => {
            if (event.column.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
              const displayedNodes = [];
              event.api.forEachNode((node) => {
                if (node.displayed) {
                  displayedNodes.push(node);
                }
              });

              const allSelected = displayedNodes.every((node) =>
                node.isSelected(),
              );
              if (allSelected) {
                event.api.deselectAll();
              } else {
                event.api.selectAll();
              }
            }
          }}
          onCellValueChanged={(event) => {
            // Only set hasChanges if the value actually changed
            if (event.oldValue !== event.newValue) {
              setHasChanges(true);
            }
          }}
          onCellEditingStarted={(event) => {
            // Store the original value when editing starts
            originalValueRef.current = event.value;

            // Set up input listener for real-time change detection
            const cellEditor = event.api.getCellEditorInstances()[0];
            if (cellEditor && cellEditor.getGui) {
              const input = cellEditor
                .getGui()
                .querySelector("input, textarea");
              if (input) {
                const handleInput = () => {
                  if (input.value !== originalValueRef.current) {
                    setHasChanges(true);
                  }
                };

                input.addEventListener("input", handleInput);
                input.addEventListener("keyup", handleInput);
                input.addEventListener("paste", handleInput);
              }
            }
          }}
          enableCellTextSelection={true}
          theme={agTheme}
          alwaysShowVerticalScroll
          rowSelection={{ mode: "multiRow", headerCheckbox: false }}
          singleClickEdit
          rowData={rows}
          columnDefs={columns}
          onCellClicked={(params) => {
            if (
              params?.column?.getColId() ===
              APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
            ) {
              const selected = params.node.isSelected();
              params.node.setSelected(!selected);
              return;
            }
          }}
          getRowId={({ data }) => data?.id}
          onRowSelected={(event) => onSelectionChanged(event)}
        />
        <IconButton
          sx={{
            height: "26px",
            width: "26px",
            padding: "0",
            border: "1px solid",
            borderColor: "divider",
            top: `min(${117 + rows.length * 359}px,calc(100% - 90px))`,
            position: "absolute",
            left: "54px",
            zIndex: 1,
            backgroundColor: "background.paper",
            boxShadow: "0px 1px 6px rgba(0, 0, 0, 0.15)",
            "&:hover": {
              backgroundColor: "action.hover",
            },
          }}
          onClick={handleAddRow}
        >
          <Iconify icon="mingcute:add-line" color="primary.main" width={18} />
        </IconButton>
        <IconButton
          sx={{
            height: "26px",
            width: "26px",
            padding: "0",
            backgroundColor: "background.paper",
            top: `calc(129px + ${359 / 2}px)`,
            right: "5px",
            transform: "translateY(-50%)",
            border: "1px solid",
            borderColor: "divider",
            position: "absolute",
            zIndex: "10",
            boxShadow: "0px 1px 6px rgba(0, 0, 0, 0.15)",
            "&:hover": {
              backgroundColor: "action.hover",
            },
          }}
          onClick={() =>
            isPending ? setOpenReload(true) : setOpenNewVariableModal(true)
          }
        >
          <Iconify icon="mingcute:add-line" color="primary.main" width={18} />
        </IconButton>
      </Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          margin: "0 0 0 auto",
          padding: "0 16px 0 16px",
          maxWidth: "510px",
          width: "100%",
        }}
      >
        <Button
          fullWidth
          variant="outlined"
          onClick={() => {
            isPending ? setOpenReload(true) : onClose();
          }}
        >
          Cancel
        </Button>
        <LoadingButton
          fullWidth
          variant="contained"
          color="primary"
          loading={isSaving}
          disabled={!hasChanges || isPending}
          onClick={() => (isPending ? setOpenReload(true) : handleSave())}
        >
          Save Variable
        </LoadingButton>
      </Box>
      <Dialog
        open={openNewVariableModal}
        onClose={handleClose}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle
          sx={{
            gap: "10px",
            display: "flex",
            flexDirection: "column",
            padding: 2,
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography fontWeight={700} fontSize="18px">
              New Variable
            </Typography>
            <IconButton onClick={handleClose}>
              <Iconify icon="mdi:close" />
            </IconButton>
          </Box>
        </DialogTitle>
        <Box component="form" onSubmit={handleSubmit(onSubmitCreateColumn)}>
          <Box
            sx={{
              display: "flex",
              gap: 1,
              alignItems: "center",
              margin: "0 0 20px 20px",
            }}
          >
            <Iconify icon="solar:info-circle-bold" color="text.disabled" />
            <Typography fontSize="12px" color="text.primary">
              Enter a name for the new variable
            </Typography>
          </Box>
          <DialogContent
            sx={{
              paddingTop: 0.5,
            }}
          >
            <FormTextFieldV2
              autoFocus
              helperText="Type the name here"
              placeholder="Enter name"
              size="small"
              control={control}
              fieldName="name"
              fullWidth
            />
          </DialogContent>
          <DialogActions sx={{ padding: 2 }}>
            <Button onClick={handleClose} variant="outlined">
              Cancel
            </Button>
            <LoadingButton variant="contained" color="primary" type="submit">
              Add
            </LoadingButton>
          </DialogActions>
        </Box>
      </Dialog>
    </Box>
  );
};

export default VariablesTable;

VariablesTable.propTypes = {
  variableNames: PropTypes.any,
  setExtractedVars: PropTypes.any,
  appliedVariableData: PropTypes.object,
  setAppliedVariableData: PropTypes.func,
  onClose: PropTypes.func.isRequired,
  gridRef: PropTypes.any,
  onSelectionChanged: PropTypes.func,
  selected: PropTypes.array,
  openDelete: PropTypes.bool,
  setOpenDelete: PropTypes.func,
  handleLabelsAdd: PropTypes.func,
  generatedData: PropTypes.object,
  setOpenReload: PropTypes.func,
  isPending: PropTypes.bool,
};
