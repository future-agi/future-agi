import { Box, useTheme } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, {
  useMemo,
  useCallback,
  memo,
  useEffect,
  useRef,
  useState,
} from "react";
import { useAgThemePrompt } from "src/hooks/use-ag-theme";
import { useWorkbenchEvaluationContext } from "../context/WorkbenchEvaluationContext";
import "./EvaluationData.css";
import {
  getColumnConfig,
  calculateRowHeight,
  CELL_STATE,
  isUnsavedRow,
} from "../common";
import { OriginTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import axios, { endpoints } from "src/utils/axios";
import { isAuthFailCloseCode } from "../../common";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import "src/components/VariableDrawer/grid.css";
import { useRunEvalMutation } from "src/sections/common/EvaluationDrawer/getEvalsList";
// import { useSocket } from "src/hooks/use-socket";
import logger from "src/utils/logger";
import EvaluationSkeleton from "../EvaluationSkeleton";
import { enqueueSnackbar } from "notistack";
import AddRowStatusPanel from "src/components/VariableDrawer/AddRowStatusPanel";
import { runPromptOverSocket } from "../../common";
import { usePromptStreamUrl } from "src/sections/workbench/createPrompt/hooks/usePromptStreamUrl";
import SingleImageViewerProvider from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider";
import DeleteRowCellRenderer from "./DeleteRowCellRenderer";

const EvaluationData = () => {
  const {
    showPrompts,
    versions,
    showVariables,
    setVariables,
    setShowVariables,
  } = useWorkbenchEvaluationContext();
  // const { socket } = useSocket();
  const theme = useTheme();
  const agThemePrompt = useAgThemePrompt();
  const { id } = useParams();
  const queryClient = useQueryClient();
  const gridApiRef = useRef(null);
  const [rows, setRows] = useState([]);
  const addRowTimeoutRef = useRef(null);
  const rowFocusTimeoutRef = useRef(null);
  const promptStreamUrl = usePromptStreamUrl();
  const activeSocketsRef = useRef({});

  const { data: evaluationData } = useQuery({
    queryKey: [
      "evaluations-workbench",
      showPrompts,
      showVariables,
      id,
      versions,
    ],
    queryFn: () =>
      axios.get(endpoints.develop.runPrompt.getEvaluationData(id), {
        params: {
          show_var: true,
          show_prompts: showPrompts,
          compare: versions.length > 1,
          versions: JSON.stringify(versions),
        },
      }),
    refetchInterval: (query) => {
      // query.state.data holds the raw axios response — select() only
      // transforms what useQuery returns, not what's cached. Unwrap it
      // here to match the shape select() produces.
      const result = query?.state?.data?.data?.result;
      if (!result) return 5000;

      let isEvalIncomplete = false;
      for (let i = 0; i < versions.length; i++) {
        if (isEvalIncomplete) break;

        const element = versions[i];
        const evalData = result[element] || {};

        // merge both sources of statuses
        const mergedStatuses = [
          // from evalStatus object values
          ...(Object.values(evalData.eval_status || {}) || []),

          // from evalOutput nested arrays -> item.status
          ...Object.values(evalData.eval_output || {})
            .flatMap((arr) => arr.map((item) => item?.status))
            .filter(Boolean),
        ];
        // check if anything is NotStarted or Running
        isEvalIncomplete = mergedStatuses.some((status) =>
          ["Running", "NotStarted", "Not Started"].includes(status),
        );
      }

      return isEvalIncomplete ? 5000 : false;
    },
    refetchOnMount: "always",
    select: (data) => {
      const result = data?.data?.result;
      for (let i = 0; i < versions.length; i++) {
        const element = versions[i];
        const evalData = result[element] || {};
        const {
          eval_output: evalOutput,
          eval_status: evalStatus,
          output,
        } = evalData;

        // Get the length of output array to determine how many dummy entries to create
        const outputLength = output?.length || 1;

        // Iterate through each evaluation ID in evalOutput
        Object.keys(evalOutput).forEach((evalId) => {
          const outputArray = evalOutput[evalId];

          // Check if the output array is empty or all entries are blank
          if (!outputArray || outputArray.length === 0) {
            // Get the status from evalStatus
            const status = evalStatus[evalId];

            if (status) {
              // Add dummy status entries based on output length
              evalOutput[evalId] = Array.from({ length: outputLength }, () => ({
                status: status,
                value: null,
                output: null,
                meta: {
                  reason: `Status: ${status}`,
                  failure: false,
                },
              }));
            }
          } else {
            // Check if any entry in the array has blank/null values
            outputArray.forEach((entry, index) => {
              if (!entry.status && evalStatus[evalId]) {
                outputArray[index] = {
                  ...entry,
                  status: evalStatus[evalId],
                };
              }
            });
          }
        });
      }
      return result;
    },
    enabled: true,
  });

  const { mutate: runEval } = useRunEvalMutation(
    id,
    () => {
      queryClient.invalidateQueries({
        queryKey: [
          "evaluations-workbench",
          showPrompts,
          showVariables,
          id,
          versions,
        ],
      });
    },
    "workbench",
  );

  const { mutate: runPromptTemplate } = useMutation({
    /**
     *
     * @param {Object} payload
     * @returns
     */
    mutationFn: (payload) => {
      const { _runRowId, ...wirePayload } = payload;
      return new Promise((resolve, reject) => {
        if (!promptStreamUrl) {
          reject(new Error("Stream URL not ready. Please try again."));
          return;
        }
        // @ts-ignore
        const socket = runPromptOverSocket({
          url: promptStreamUrl,
          payload: wirePayload,
          onMessage: (data) => {
            setWsData(data);
            if (data?.streaming_status === "all_completed") {
              resolve(data);
            }
          },
          onError: (err) => {
            activeSocketsRef.current[_runRowId]?.close();
            delete activeSocketsRef.current[_runRowId];
            reject(err);
          },
          onClose: (event) => {
            if (isAuthFailCloseCode(event)) {
              reject(new Error(event?.reason || "Permission denied"));
            } else {
              reject(new Error("Stream connection closed unexpectedly"));
            }
          },
        });
        if (_runRowId != null) activeSocketsRef.current[_runRowId] = socket;
      });
    },
  });

  const resultSetter = useCallback(
    (id, index, status, chunk) => {
      // run_index is positional, and row ids drift from position after a
      // middle-row delete — resolve the streaming target by position.
      const rowNode = gridApiRef.current?.api?.getDisplayedRowAtIndex(index);
      switch (status) {
        case "started": {
          rowNode?.setDataValue(id, CELL_STATE.LOADING);
          break;
        }
        case "running": {
          let exisitingResult = rowNode.data[id];
          if (exisitingResult === CELL_STATE.LOADING) exisitingResult = "";
          exisitingResult += chunk;
          rowNode?.setDataValue(id, exisitingResult);
          break;
        }
        case "completed":
          break;
        case "all_completed": {
          const rowId = rowNode?.data?.id;
          if (rowId != null && rowId in activeSocketsRef.current) {
            activeSocketsRef.current[rowId]?.close();
            delete activeSocketsRef.current[rowId];
          }
          break;
        }
        default:
          break;
      }
    },
    [gridApiRef],
  );

  const setWsData = useCallback(
    (event) => {
      try {
        const wsData = event;
        if (wsData?.type === "error") {
          enqueueSnackbar(wsData?.message || "Something went wrong", {
            variant: "error",
          });
          return;
        }
        if (wsData?.type !== "run_prompt") {
          return;
        }

        const version = wsData?.version;
        const resultIndex = wsData?.result_index;
        const streamingStatus = wsData?.streaming_status;
        resultSetter(
          `Output-${version}`,
          resultIndex,
          streamingStatus,
          wsData?.chunk,
        );
      } catch (err) {
        logger.error("Error parsing WebSocket data:", err);
      }
    },
    [
      resultSetter,
      // setResultsByIndex,
      // setLoadingStatusByIndex,
      // selectedVersions,
      // loadingStatus,
    ],
  );

  // useEffect(() => {
  //   if (socket) {
  //     socket.addEventListener("message", setWsData);

  //     return () => {
  //       if (socket) {
  //         socket.removeEventListener("message", setWsData);
  //       }
  //     };
  //   }
  // }, [socket, setWsData]);

  const handleCellClick = useCallback(
    (originType, index, version, evalTemplateId = "") => {
      const api = gridApiRef?.current?.api;
      api?.stopEditing();
      const { variables } = evaluationData;
      const runPromptPayload = {
        type: "run_template",
        version: version,
        run_index: index,
        is_run: "prompt",
        template_id: id,
        // FE-only: stable row id to key the active socket (stripped before send).
        _runRowId: api?.getDisplayedRowAtIndex(index)?.data?.id,
      };
      let emptyField = false;
      if (variables !== null) {
        const rowCount = api?.getDisplayedRowCount?.() ?? 0;
        const variableKeys = Object.keys(variables ?? {});
        // Build positionally from the live grid rows so the arrays cover every
        // row — reusing evaluationData.variables crashed on added/deleted rows.
        const variableNames = {};
        for (const key of variableKeys) {
          const persisted = variables[key] ?? [];
          variableNames[key] = Array.from({ length: rowCount }, (_, i) => {
            const cell = showVariables
              ? api?.getDisplayedRowAtIndex(i)?.data?.[key]
              : persisted[i];
            // EMPTY sentinel counts as empty so the run is blocked, not sent.
            return cell == null || cell === CELL_STATE.EMPTY
              ? ""
              : `${cell}`.trim();
          });
        }
        runPromptPayload["variable_names"] = variableNames;
        emptyField = Object.values(variableNames).some(
          (item) => !item[index]?.trim(),
        );
      }
      // else if (
      //   (variables === null || variables === null) &&
      //   variableData !== null
      // ) {
      //   const variables = { ...variableData };
      //   const variableKeys = Object.keys(variables ?? {});
      //   const variableValues = Object.values(variables ?? {});
      //   if (lastRowIndex === variableValues[0]?.length) {
      //     for (let i = 0; i < variableKeys.length; i++) {
      //       const key = variableKeys[i];
      //       variables[key].push("");
      //     }
      //   }
      //   runPromptPayload["variable_names"] = variables;
      //   emptyField = Object.values(
      //     runPromptPayload["variable_names"] || {},
      //   ).some((item) => !item[index].trim());
      // }

      switch (originType) {
        case OriginTypes.EVALUATION:
          runEval({
            version_to_run: [version],
            run_index: index,
            prompt_eval_config_ids: [evalTemplateId],
          });
          break;
        default:
          if (!emptyField) {
            // saveOrRunPromptTemplate(runPromptPayload);
            delete runPromptOverSocket.variable_names;
            runPromptTemplate(runPromptPayload);
          } else {
            enqueueSnackbar("Please enter all variables", {
              variant: "warning",
            });
          }
          break;
      }
    },
    [evaluationData, id, runEval, runPromptTemplate, showVariables],
  );

  // Memoize the data processing functions
  const processColumnConfig = useCallback(
    (data) => {
      if (!data) {
        return [
          {
            field: "Variables",
            cellRenderer: () => <EvaluationSkeleton />,
          },
          {
            field: "Outputs",
            cellRenderer: () => <EvaluationSkeleton />,
          },
          {
            field: "Evaluations",
            cellRenderer: () => <EvaluationSkeleton />,
          },
          {
            field: "Comparisons",
            cellRenderer: () => <EvaluationSkeleton />,
          },
        ];
      }

      const colDefs = [];
      const { variables, ...versionData } = data;
      const variableKeys = Object.keys(variables ?? {});
      const versionNames = Object.keys(versionData ?? {});
      const addedEvals = versionData[versionNames[0]].eval_names ?? [];
      setVariables(variables);

      // Process variable columns
      variableKeys.forEach((element) => {
        colDefs.push(
          getColumnConfig({
            id: element,
            name: `{{${element}}}`,
            hide: !showVariables,
            originType: OriginTypes.VARIABLE,
            showPrompts: showPrompts,
            editable: true,
          }),
        );
      });

      // Process version columns
      versionNames.forEach((element) => {
        colDefs.push(
          getColumnConfig({
            id: `Output-${element}`,
            name: `Output`,
            model_detail: versionData[element].model_detail,
            template_version: element,
            originType: OriginTypes.RUN_PROMPT,
            minWidth: 450,
            showPrompts: showPrompts,
            handleClick: handleCellClick,
            messages: versionData[element].messages,
          }),
        );
      });

      // Process evaluation columns
      addedEvals.forEach((evalName) => {
        const status = versionData[versionNames[0]].eval_status[evalName.id];
        const children = versionNames.map((element) => ({
          id: `${evalName.id}-${element}`,
          name: element,
          template_version: element,
          originType: OriginTypes.EVALUATION,
          handleClick: handleCellClick,
          reverseOutput: evalName.reverse_output,
          status: status,
          minWidth: 200,
          evalTemplateId: evalName.id,
          choicesMap: evalName.choices_map,
        }));

        colDefs.push(
          getColumnConfig({
            id: evalName.id,
            name: evalName.name,
            originType: OriginTypes.EVALUATION,
            children: children,
            minWidth: 200,
            showPrompts: showPrompts,
          }),
        );
      });

      if (versionNames.length < 3) {
        colDefs.push(
          getColumnConfig({
            id: "Comparison",
            name: "Add comparison",
            showPrompts: showPrompts,
          }),
        );
      }

      return colDefs;
    },
    [handleCellClick, setVariables, showVariables, showPrompts],
  );

  const processRowData = useCallback((data) => {
    if (!data) {
      return [
        { Variables: "", Outputs: "", Evaluations: "", Comparison: "" },
        { Variables: "", Outputs: "", Evaluations: "", Comparison: "" },
        { Variables: "", Outputs: "", Evaluations: "", Comparison: "" },
        { Variables: "", Outputs: "", Evaluations: "", Comparison: "" },
        { Variables: "", Outputs: "", Evaluations: "", Comparison: "" },
        { Variables: "", Outputs: "", Evaluations: "", Comparison: "" },
      ];
    }

    const rows = [];
    const { variables, ...versionData } = data;
    const variableKeys = Object.keys(variables ?? {});
    const variableValues = Object.values(variables ?? {});
    let noOfRows = variableValues?.[0]?.length ?? 1;
    const versionNames = Object.keys(versionData);
    const versionValues = Object.values(versionData);
    const addedEvals = versionData[versionNames[0]].eval_names;
    for (let i = 0; i < versionValues.length; i++) {
      noOfRows = Math.max(versionValues[i]?.output?.length, noOfRows);
    }

    for (let i = 0; i < noOfRows; i++) {
      const row = {
        id: `${i}`,
      };

      // Process variable data
      variableKeys.forEach((element) => {
        row[element] = variables[element][i] ?? "";
      });

      // Process version data
      versionNames.forEach((element) => {
        row[`Output-${element}`] = versionData[element]?.output[i] ?? "";
      });

      // Process evaluation data
      addedEvals.forEach((evals) => {
        versionNames.forEach((version) => {
          const addedEvalValue =
            versionData[version].eval_output[evals.id]?.[i] ?? "";
          row[`${evals.id}-${version}`] = addedEvalValue;
        });
      });

      rows.push(row);
    }

    return rows;
  }, []);

  const handleDeleteRow = useCallback((rowId) => {
    // Close any in-flight run for this row so it can't stream onto a stale row.
    activeSocketsRef.current[rowId]?.close();
    delete activeSocketsRef.current[rowId];
    setRows((prev) =>
      prev.length > 1 ? prev.filter((row) => row.id !== rowId) : prev,
    );
  }, []);

  const hasDeletableRow = rows.length > 1 && rows.some(isUnsavedRow);

  const columnConfig = useMemo(() => {
    const cols = processColumnConfig(evaluationData);
    // Show the delete gutter only when an unsaved local row exists.
    if (evaluationData && hasDeletableRow) {
      cols.push({
        colId: "rowActions",
        headerName: "",
        pinned: "right",
        width: 48,
        minWidth: 48,
        maxWidth: 48,
        resizable: false,
        sortable: false,
        editable: false,
        suppressMovable: true,
        cellRenderer: DeleteRowCellRenderer,
        cellRendererParams: { onDelete: handleDeleteRow },
        cellStyle: {
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 0,
        },
      });
    }
    return cols;
  }, [evaluationData, processColumnConfig, hasDeletableRow, handleDeleteRow]);

  useEffect(() => {
    setRows((prev) => {
      const next = processRowData(evaluationData);
      const unsavedTail = prev.slice(next.length).filter(isUnsavedRow);
      if (!unsavedTail.length) return next;
      return [...next, ...unsavedTail];
    });
    return () => {
      if (addRowTimeoutRef.current) {
        clearTimeout(addRowTimeoutRef.current);
      }
      if (rowFocusTimeoutRef.current) {
        clearTimeout(rowFocusTimeoutRef.current);
      }
    };
  }, [evaluationData, processRowData]);

  useEffect(() => {
    return () => {
      Object.values(activeSocketsRef.current).forEach((socket) => {
        if (socket) socket.close();
      });
      activeSocketsRef.current = {};
    };
  }, []);

  const defaultColDef = useMemo(
    () => ({
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,

      filter: false,
      resizable: true,
      lockVisible: true,
      flex: 1,
      cellStyle: {
        padding: 0,
        display: "flex",
        flex: 1,
        flexDirection: "column",
      },
    }),
    [],
  );

  const handleAddRows = useCallback(
    (count = 1) => {
      const { id, ...lastRow } = rows.at(-1);
      const outputCells = Object.entries(lastRow).filter(([key, _]) =>
        key.startsWith("Output"),
      );
      const isPreviousVersionRun = outputCells.some(
        (val) => val[1].trim() !== "",
      );
      if (!isPreviousVersionRun) {
        enqueueSnackbar({
          message: "Generate an output of the previous row to add a new row",
          variant: "info",
        });
        return;
      }
      setShowVariables(true);
      if (addRowTimeoutRef.current) {
        clearTimeout(addRowTimeoutRef.current);
      }
      if (rowFocusTimeoutRef.current) {
        clearTimeout(rowFocusTimeoutRef.current);
      }
      addRowTimeoutRef.current = setTimeout(() => {
        setRows((prev) => {
          // max(id)+1 keeps ids unique after a middle row is deleted.
          let nextId =
            prev.reduce((max, row) => Math.max(max, Number(row.id) || 0), -1) +
            1;
          const newRows = Array.from({ length: count }, (_) => ({
            ...Object.keys(rows[0]).reduce((acc, key) => {
              if (key === "id") {
                acc["id"] = `${nextId++}`;
              } else if (key !== "_isLocal") {
                acc[key] = CELL_STATE.EMPTY;
              }
              return acc;
            }, {}),
            // Mark as a locally-added (unsaved) row — deletable until persisted.
            _isLocal: true,
          }));
          return [...prev, ...newRows];
        });
      }, 200);
      rowFocusTimeoutRef.current = setTimeout(() => {
        const columnId = Object.keys(rows[0])[1];
        const displayedRowIndex =
          gridApiRef.current?.api?.getLastDisplayedRowIndex();
        gridApiRef.current?.api?.setFocusedCell(displayedRowIndex, columnId);
      }, 300);
    },
    [rows, setShowVariables],
  );

  const statusBar = useMemo(() => {
    const { variables } = evaluationData ?? {};
    if (rows.length >= 10 || Object.keys(variables ?? {}).length === 0)
      return undefined;

    return {
      statusPanels: [
        {
          statusPanel: AddRowStatusPanel,
          align: "left",
          statusPanelParams: {
            handleAddRow: handleAddRows,
          },
        },
      ],
    };
  }, [rows, evaluationData, handleAddRows]);

  const gridTheme = useMemo(
    () =>
      agThemePrompt.withParams({
        columnBorder: true,
        borderColor: theme.palette.divider,
        headerBackgroundColor: theme.palette.background.paper,
        headerHeight: "39px",
        cellTextColor: theme.palette.text.primary,
        wrapperBorderRadius: theme.spacing(1),
      }),
    [agThemePrompt, theme],
  );

  const getRowHeight = useCallback((params) => {
    return calculateRowHeight(params.data);
  }, []);

  return (
    <SingleImageViewerProvider>
      <Box
        mt={1}
        display={"flex"}
        flexDirection={"column"}
        gap={2}
        overflow={"auto"}
        sx={{
          "& .ag-cell p": {
            lineHeight: 1.5,
            margin: 1,
          },
          "& .ag-cell-wrapper": {
            lineHeight: 1.5,
          },
          "& .ag-cell": {
            "&::-webkit-scrollbar": {
              display: "none",
            },
            "-ms-overflow-style": "none", // IE and Edge
            "scrollbar-width": "none", // Firefox
          },
          "& .MuiBox-root": {
            "&::-webkit-scrollbar": {
              display: "none",
            },
            "-ms-overflow-style": "none", // IE and Edge
            "scrollbar-width": "none", // Firefox
          },
        }}
      >
        <Box className="ag-theme-quartz prompt-variable-gird">
          <AgGridReact
            ref={gridApiRef}
            stopEditingWhenCellsLoseFocus
            rowData={rows}
            theme={gridTheme}
            getRowHeight={getRowHeight}
            animateRows
            domLayout="autoHeight"
            className="prompt-evaluation-gird"
            suppressContextMenu
            columnDefs={columnConfig}
            defaultColDef={defaultColDef}
            getRowId={(params) => params.data.id}
            statusBar={statusBar}
          />
        </Box>
      </Box>
    </SingleImageViewerProvider>
  );
};

EvaluationData.displayName = "EvaluationData";

export default memo(EvaluationData);
