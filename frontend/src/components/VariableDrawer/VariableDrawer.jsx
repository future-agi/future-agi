import {
  Box,
  Button,
  Drawer,
  FormHelperText,
  IconButton,
  Skeleton,
  Typography,
} from "@mui/material";
import React, {
  useCallback,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import Iconify from "../iconify";
import { LoadingButton } from "@mui/lab";
import { AgGridReact } from "ag-grid-react";
import { useAgThemePromptWith } from "src/hooks/use-ag-theme";
import "./grid.css";
import DeleteAction from "./Renderers/DeleteAction";
import SvgColor from "../svg-color";
import AddRowsBox from "./AddRowsBox";
import { ShowComponent } from "src/components/show";
import EmptyVariable from "./EmptyVariable";
import { usePromptWorkbenchContext } from "src/sections/workbench/createPrompt/WorkbenchContext";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import {
  GeneratePromptButton,
  GeneratePromptButtonIcon,
} from "../PromptCards/PromptCardStyleComponents";
import GeneratePromptConfirmation from "./GeneratePromptConfirmation";
import { ConfirmDialog } from "../custom-dialog";
import AddRowStatusPanel from "./AddRowStatusPanel";
// import PlaceholdersList from "./PlaceholdersList";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const VARIABLE_DRAWER_THEME_PARAMS = { wrapperBorderRadius: "4px" };

const generate_choices = {
  ADD_TO_REMAINING: "add_to_remaining",
  GENERATE_ALL: "generate_all",
};

const skeletonCellRenderer = () => {
  return (
    <div style={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Skeleton
        variant="rounded"
        animation="wave"
        width="100%"
        height={12}
        sx={{
          bgcolor: "background.neutral",
          borderRadius: "4px",
        }}
      />
    </div>
  );
};

const VariableDrawerChild = React.forwardRef(
  (
    {
      onClose,
      variables,
      variableData,
      setVariableData,
      onOpenImportDatasetDrawer,
    },
    ref,
  ) => {
    const agThemePrompt = useAgThemePromptWith(VARIABLE_DRAWER_THEME_PARAMS);
    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        sortable: false,
        filter: false,
        resizable: true,
        minWidth: 150,
        suppressMenuHide: true,
        suppressCheckbox: true,
        suppressHeaderMenuButton: true,
        suppressHeaderContextMenu: true,
        cellSelection: false,
      }),
      [],
    );

    const { role: userRole } = useAuthContext();

    const {
      valuesChanged,
      setValuesChanged,
      promptName,
      prompts,
      // placeholders,
      // placeholderData,
      originalPlaceholderData,
      // setOriginalPlaceholderData,
      setPlaceholderData,
      // submitPlaceholders,
    } = usePromptWorkbenchContext();
    const originalValueRef = useRef(null);
    const [confirmationOpen, setConfirmationOpen] = useState(false);
    const [closeConfirmationOpen, setCloseConfirmationOpen] = useState(false);
    // const [hasPlaceholderError, setHasPlaceholderError] = useState(false);
    const [isError, setIsError] = useState(false);
    const [loadingRows, setLoadingRows] = useState([]);
    const [isGenerating, setIsGenerating] = useState(false);
    const gridRef = useRef(null);
    const [rows, setRows] = useState(() => {
      const maxLength = Math.max(
        ...Object.values(variableData).map((value) => value.length),
      );
      const rows = [];
      for (let i = 0; i < maxLength; i++) {
        const row = {};
        Object.keys(variableData).forEach((key) => {
          row[key] = variableData?.[key]?.[i] || "";
        });
        rows.push(row);
      }
      if (rows.length === 0) {
        rows.push({
          ...Object.keys(variableData).reduce((acc, key) => {
            acc[key] = "";
            return acc;
          }, {}),
        });
      }
      return rows;
    });

    const getCompleteRowsCount = (rowsData) => {
      return rowsData.filter((row) =>
        variables.every((variable) => {
          const val = row[variable];
          return val !== undefined && val !== null && val !== "";
        }),
      ).length;
    };

    const hasCompleteRowData = (row) => {
      return variables.every((variable) => {
        const val = row[variable];
        return val !== undefined && val !== null && val !== "";
      });
    };

    const hasAnyData = (rowsData) => {
      return rowsData.some((row) =>
        Object.values(row).some(
          (val) => val !== undefined && val !== null && val !== "",
        ),
      );
    };

    const dynamicCellRenderer = useCallback(
      (params) => {
        const rowIndex = params.node.rowIndex;
        const isLoadingRow = loadingRows.includes(rowIndex) && isGenerating;

        if (isLoadingRow) {
          return skeletonCellRenderer();
        }

        return params.value || "";
      },
      [isGenerating, loadingRows],
    );

    const handleDeleteRow = useCallback(
      (rowIndex) => {
        setRows((prev) => {
          const updated = prev.filter((_, index) => index !== rowIndex);
          setValuesChanged(true);
          setIsError(false);
          return updated;
        });
      },
      [setValuesChanged],
    );

    const colDefs = useMemo(() => {
      const cols = [
        {
          headerName: "",
          field: "srno",
          width: 50,
          minWidth: 50,
          maxWidth: 50,
          valueGetter: (p) => {
            return p.node.rowIndex + 1;
          },
          pinned: "left",
        },
      ];
      variables.forEach((variable) => {
        //@ts-ignore
        cols.push({
          headerName: `{{${variable}}}`,
          field: variable,
          flex: 1,
          minWidth: 200,
          maxWidth: 300,
          editable: (params) => {
            if (!RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole])
              return false;

            return !loadingRows.includes(params.node.rowIndex) || !isGenerating;
          },
          cellRenderer: dynamicCellRenderer,
          // Use valueGetter and valueSetter to handle special characters in field names
          valueGetter: (params) => {
            return params.data?.[variable];
          },
          valueSetter: (params) => {
            if (params.data) {
              params.data[variable] = params.newValue;
              return true;
            }
            return false;
          },
        });
      });
      //@ts-ignore
      cols.push({
        headerName: "",
        field: "delete",
        width: 50,
        minWidth: 50,
        maxWidth: 50,
        cellRenderer: DeleteAction,
        cellStyle: {
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          padding: "0px",
        },
        cellRendererParams: {
          handleDeleteRow,
          isGenerating,
          disabled: !RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole],
        },
        pinned: "right",
      });
      return cols;
    }, [
      variables,
      handleDeleteRow,
      isGenerating,
      dynamicCellRenderer,
      loadingRows,
      userRole,
    ]);

    const handleSave = async () => {
      const gridApi = gridRef.current?.api;
      const _variableData = variables.reduce((acc, key) => {
        acc[key] = [];
        return acc;
      }, {});
      let isEmpty = false;
      gridApi.forEachNode((node) => {
        const data = node.data;
        if (Object.values(data).every((value) => value === "")) return;
        Object.keys(data).forEach((key) => {
          if (!variables.includes(key)) return;
          if (!data[key]) {
            isEmpty = true;
          }
          _variableData[key].push(data[key]);
        });
      });
      if (isEmpty) {
        setIsError(true);
        return;
      }
      setValuesChanged(false);
      setVariableData(_variableData);
      onClose();
    };

    // const handleSubmitPlaceholders = async () => {
    //   try {
    //     await submitPlaceholders();
    //     setOriginalPlaceholderData(placeholderData);
    //     onClose();
    //   } catch (e) {
    //     setIsError(true);
    //   }
    // };

    const handleAddRow = useCallback(() => {
      setRows((prev) => {
        return [
          ...prev,
          {
            ...Object.keys(variableData).reduce((acc, key) => {
              acc[key] = "";
              return acc;
            }, {}),
          },
        ];
      });
      setIsError(false);
    }, [variableData]);

    const handleAddRows = (count = 0) => {
      setRows((prev) => {
        const newRows = Array.from({ length: count }, (_) => ({
          ...Object.keys(variableData).reduce((acc, key) => {
            acc[key] = "";
            return acc;
          }, {}),
        }));
        return [...prev, ...newRows];
      });
    };

    const statusBar = useMemo(() => {
      if (rows.length >= 10) return {};

      return {
        statusPanels: [
          {
            statusPanel: AddRowStatusPanel,
            align: "left",
            statusPanelParams: {
              handleAddRow,
            },
          },
        ],
      };
    }, [rows, handleAddRow]);

    const { mutate: generateData, isPending } = useMutation({
      mutationFn: (generationChoice) => {
        const currentRowCount = rows.length;
        const currentCompleteRowsCount = getCompleteRowsCount(rows);

        let variable_count = 0;

        if (generationChoice === generate_choices.ADD_TO_REMAINING) {
          // Generate data only for rows that don't have complete data
          variable_count = currentRowCount - currentCompleteRowsCount;
        } else {
          // Generate data for all existing rows
          variable_count = currentRowCount;
        }

        setIsGenerating(true);

        if (generationChoice === generate_choices.ADD_TO_REMAINING) {
          const existingCompleteRows = rows.filter(hasCompleteRowData);
          const incompleteRows = rows.filter((row) => !hasCompleteRowData(row));
          const startIndex = existingCompleteRows.length;
          const loadingRowIndices = Array.from(
            { length: incompleteRows.length },
            (_, i) => startIndex + i,
          );
          setLoadingRows(loadingRowIndices);

          // Keep complete rows and create placeholders for incomplete ones
          const placeholderRows = Array.from(
            { length: incompleteRows.length },
            () => ({
              ...Object.keys(variableData).reduce((acc, key) => {
                acc[key] = "";
                return acc;
              }, {}),
            }),
          );

          setRows([...existingCompleteRows, ...placeholderRows]);
        } else {
          // For regenerate all, show loading for all existing rows
          const loadingRowIndices = Array.from(
            { length: currentRowCount },
            (_, i) => i,
          );
          setLoadingRows(loadingRowIndices);

          const placeholderRows = Array.from(
            { length: currentRowCount },
            () => ({
              ...Object.keys(variableData).reduce((acc, key) => {
                acc[key] = "";
                return acc;
              }, {}),
            }),
          );

          setRows(placeholderRows);
        }

        setTimeout(() => {
          if (gridRef.current?.api) {
            gridRef.current.api.redrawRows();
          }
        }, 100);
        const promptInstructions = prompts.map((item) => item.prompts);
        return axios.post(endpoints.develop.runPrompt.generateVariables, {
          prompt_name: promptName,
          variable_names: variables,
          variable_count,
          prompt_instructions: promptInstructions,
          generation_type: "prompt",
        });
      },
      onSuccess: (data, generationChoice) => {
        const variablesData = data?.data?.result?.variables;
        const values = Object.values(variablesData ?? {});
        const keys = Object.keys(variablesData ?? {});
        const totalInputs = values[0];

        if (totalInputs?.length > 0) {
          const newRows = [];
          for (let i = 0; i < totalInputs?.length; i++) {
            const entry = {};
            for (let j = 0; j < values.length; j++) {
              entry[keys[j]] = values[j][i];
            }
            newRows.push(entry);
          }

          const rowsWithIds = newRows.map((item, index) => ({
            ...item,
            id: index,
          }));

          let finalRows = [];

          if (generationChoice === generate_choices.ADD_TO_REMAINING) {
            // Keep complete rows and add new generated rows for incomplete ones
            const existingCompleteRows = rows.filter(hasCompleteRowData);
            finalRows = [...existingCompleteRows, ...rowsWithIds];
          } else {
            // Replace all rows with generated data
            finalRows = rowsWithIds;
          }

          setRows(finalRows);
          setValuesChanged(true);
        }
        setLoadingRows([]);
        setIsGenerating(false);
      },
      onError: () => {
        setLoadingRows([]);
        setIsGenerating(false);
      },
    });

    // Updated handleGenerateDataClick function
    const handleGenerateDataClick = () => {
      const hasData = hasAnyData(rows);
      const currentRowCount = rows.length;

      if (hasData && currentRowCount > 0) {
        setConfirmationOpen(true);
      } else if (currentRowCount > 0) {
        // Generate data for existing empty rows
        generateData(generate_choices.GENERATE_ALL);
      } else {
        // No rows exist, create one row and generate data for it
        const newRow = {
          ...Object.keys(variableData).reduce((acc, key) => {
            acc[key] = "";
            return acc;
          }, {}),
        };
        setRows([newRow]);
        setTimeout(() => {
          generateData(generate_choices.GENERATE_ALL);
        }, 100);
      }
    };
    const handleConfirmationClose = (choice) => {
      setConfirmationOpen(false);
      if (choice) {
        generateData(choice);
      }
    };

    const handleClose = useCallback(() => {
      if (valuesChanged || isGenerating || isPending) {
        setCloseConfirmationOpen(true);
      } else {
        onClose();
      }
    }, [isGenerating, isPending, onClose, valuesChanged]);

    const handleCloseConfirmation = (confirmed) => {
      setCloseConfirmationOpen(false);
      if (confirmed) {
        // User confirmed to close without saving
        setPlaceholderData(originalPlaceholderData);
        onClose();
      }
    };

    useImperativeHandle(
      ref,
      () => ({
        handleClose,
      }),
      [handleClose],
    );

    const currentCompleteRowsCount = getCompleteRowsCount(rows);

    // const hasPlaceholders =
    //   Array.isArray(placeholders) &&
    //   placeholders.length > 0 &&
    //   placeholders.some((p) => (Array.isArray(p) ? p.length > 0 : !!p));

    const hasPlaceholders = false;

    return (
      <>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
          }}
        >
          {/* Header */}
          <Box
            display="flex"
            justifyContent="space-between"
            alignItems="center"
            position="sticky"
            top={0}
            zIndex={10}
            bgcolor="background.paper"
            padding={2}
            borderBottom="1px solid"
            borderColor="background.neutral"
          >
            <Typography typography="m3" fontWeight="fontWeightSemiBold">
              {variables?.length === 0 && hasPlaceholders
                ? "Placeholders"
                : "Variables"}
            </Typography>

            <Box display="flex" gap={1}>
              <ShowComponent condition={variables.length > 0}>
                <Button
                  aria-label="open-import-dataset"
                  size="small"
                  disabled={
                    isPending ||
                    !RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]
                  }
                  startIcon={
                    <SvgColor
                      src="/assets/icons/navbar/hugeicons.svg"
                      width="16px"
                      sx={{ color: "text.primary" }}
                    />
                  }
                  onClick={onOpenImportDatasetDrawer}
                  sx={{
                    color: "text.primary",
                    border: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  Import Dataset
                </Button>
                <GeneratePromptButton
                  onClick={handleGenerateDataClick}
                  disabled={
                    isPending ||
                    !RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]
                  }
                  startIcon={<GeneratePromptButtonIcon />}
                  size="small"
                  borderRadius="8px"
                  padding="5px 12px"
                  height="auto"
                >
                  Generate Sample Data
                </GeneratePromptButton>
              </ShowComponent>

              {/* Close Button */}
              <IconButton onClick={handleClose}>
                <Iconify icon="solar:close-linear" color="text.primary" />
              </IconButton>
            </Box>
          </Box>

          {/* Scrollable Content */}
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 2,
              flex: 1,
              overflow: "auto",
              padding: 2,
            }}
          >
            {/* Show Empty only when both are empty */}
            <ShowComponent
              condition={variables.length === 0 && !hasPlaceholders}
            >
              <EmptyVariable />
            </ShowComponent>

            {/* Variables section - only show when variables exist */}
            <ShowComponent condition={variables.length > 0}>
              <Box className="ag-theme-quartz prompt-variable-gird">
                <AgGridReact
                  columnDefs={colDefs}
                  theme={agThemePrompt}
                  rowData={rows}
                  defaultColDef={defaultColDef}
                  domLayout="autoHeight"
                  onCellValueChanged={(event) => {
                    if (event.oldValue !== event.newValue) {
                      if (valuesChanged) return;
                      setValuesChanged(true);
                    }
                  }}
                  onCellEditingStarted={(event) => {
                    originalValueRef.current = event.value;
                    const cellEditor = event.api.getCellEditorInstances()[0];
                    if (cellEditor && cellEditor.getGui) {
                      const input = cellEditor
                        .getGui()
                        .querySelector("input, textarea");
                      if (input) {
                        const handleInput = () => {
                          if (input.value !== originalValueRef.current) {
                            if (!valuesChanged) {
                              setValuesChanged(true);
                            }
                          }
                        };
                        input.addEventListener("input", handleInput);
                        input.addEventListener("keyup", handleInput);
                        input.addEventListener("paste", handleInput);
                      }
                    }
                  }}
                  ref={gridRef}
                  statusBar={
                    RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]
                      ? statusBar
                      : undefined
                  }
                  singleClickEdit
                  stopEditingWhenCellsLoseFocus
                />
              </Box>

              <ShowComponent condition={isError}>
                <FormHelperText error sx={{ margin: 0 }}>
                  Variables cannot be empty
                </FormHelperText>
              </ShowComponent>

              <ShowComponent
                condition={RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]}
              >
                <AddRowsBox
                  handleAddRows={handleAddRows}
                  currentRows={rows.length}
                />
              </ShowComponent>
            </ShowComponent>

            {/* Placeholders section */}
            {/* <ShowComponent condition={hasPlaceholders}>
              <ShowComponent condition={variables.length > 0}>
                <Typography
                  variant="m3"
                  width="100%"
                  fontWeight="fontWeightSemiBold"
                  mt={2}
                >
                  Placeholders
                </Typography>
              </ShowComponent>
              <PlaceholdersList onValidationChange={setHasPlaceholderError} />
            </ShowComponent> */}
          </Box>

          {/* Sticky Footer - only show when variables exist */}
          <ShowComponent condition={variables.length > 0 || hasPlaceholders}>
            <Box
              sx={{
                display: "flex",
                justifyContent: "flex-end",
                padding: 2,
                paddingTop: 1,
                bgcolor: "background.paper",
                borderTop: "1px solid",
                borderColor: "background.neutral",
                position: "sticky",
                bottom: 0,
                zIndex: 5,
              }}
            >
              <LoadingButton
                variant="contained"
                color="primary"
                sx={{ minWidth: "200px" }}
                // disabled={!valuesChanged && hasPlaceholderError}
                disabled={!valuesChanged}
                loading={isPending}
                onClick={async () => {
                  // await handleSubmitPlaceholders();
                  await handleSave();
                }}
              >
                Save
              </LoadingButton>
            </Box>
          </ShowComponent>
        </Box>

        {/* Confirmation dialogs */}
        <GeneratePromptConfirmation
          open={confirmationOpen}
          onClose={handleConfirmationClose}
          totalRowCount={rows.length}
          incompleteRowCount={rows.length - currentCompleteRowsCount}
        />
        <ConfirmDialog
          open={closeConfirmationOpen}
          onClose={() => handleCloseConfirmation(false)}
          title="Unsaved Changes"
          content={
            isGenerating || isPending
              ? "Data is currently being generated. Are you sure you want to close?"
              : "You have unsaved changes. Are you sure you want to close without saving?"
          }
          action={
            <Button
              size="small"
              variant="contained"
              color="error"
              onClick={() => handleCloseConfirmation(true)}
            >
              Confirm
            </Button>
          }
        />
      </>
    );
  },
);

VariableDrawerChild.displayName = "VariableDrawerChild";

VariableDrawerChild.propTypes = {
  onClose: PropTypes.func,
  variables: PropTypes.array,
  variableData: PropTypes.object,
  setVariableData: PropTypes.func,
  onOpenImportDatasetDrawer: PropTypes.func,
};

const VariableDrawer = ({
  open,
  onClose,
  variables,
  variableData,
  setVariableData,
  onOpenImportDatasetDrawer,
}) => {
  const childRef = useRef(null);
  const covered = useDrawerCovered(open);

  const handleDrawerClose = () => {
    if (childRef.current?.handleClose) {
      childRef.current.handleClose();
    } else {
      // Fallback to regular close if ref is not available
      onClose();
    }
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleDrawerClose}
      sx={coveredDrawerSx(covered)}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          width: "85vw",
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
      <VariableDrawerChild
        ref={childRef}
        onClose={onClose}
        variables={variables}
        variableData={variableData}
        setVariableData={setVariableData}
        onOpenImportDatasetDrawer={onOpenImportDatasetDrawer}
      />
    </Drawer>
  );
};

VariableDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  variables: PropTypes.array,
  variableData: PropTypes.object,
  setVariableData: PropTypes.func,
  onOpenImportDatasetDrawer: PropTypes.func,
};

export default VariableDrawer;
