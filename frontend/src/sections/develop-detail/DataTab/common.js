import CustomDevelopDetailColumn from "src/sections/common/CustomDevelopDetailColumn";
import { parseCellValue } from "src/utils/agUtils";
import {
  AGGridCellDataType,
  DevelopDataBlockedChangeDataType,
} from "src/utils/constant";
import CustomCellRender from "src/sections/common/DevelopCellRenderer/CustomCellRender";
import CustomDevelopGroupCellHeader from "src/sections/common/DevelopCellRenderer/CustomDevelopGroupCellHeader";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import {
  reorderMenuList,
  setMenuIcons,
} from "src/utils/MenuIconSet/setMeniIcons";
import { menuIcons } from "src/utils/MenuIconSet/svgIcons";
import {
  useAddColumnApiCallStore,
  useAddEvaluationFeebackStore,
  useConditionalNodeStore,
  useExtractEntitiesStore,
  useDeleteColumnStore,
  useEditCellStore,
  useEditColumnNameStore,
  useEditColumnTypeStore,
  useImprovePromptStore,
  useRetrievalStore,
  useRunEvaluationStore,
  useRunPromptStore,
  useShowSummaryStore,
  useExtractJsonKeyStore,
  useExecuteCodeStore,
  useClassificationStore,
} from "../states";
import axios, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";
import { format } from "date-fns";
import { StatusTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import { enqueueSnackbar } from "notistack";

const STATUS = {
  PASSED: "Passed",
  FAILED: "Failed",
};

// Column-level statuses that mean the column's cells are still being
// (re)computed server-side. Single source of truth shared by the grid poll
// (DevelopDataV2) and the optimistic loading helper below so they never drift.
export const RefreshStatus = [
  "Running",
  "NotStarted",
  "Editing",
  "ExperimentEvaluation",
  "PartialRun",
];

// A just-added column carries a refreshing column-status before the async
// worker flips its cells to "running", so those cells render blank instead of a
// loading skeleton. For every refreshing column, mark its not-yet-populated
// cells as running so the skeleton shows immediately; cells that already have a
// value/error/running status are left for the next server poll to replace.
export const withPendingColumnLoadingState = (rows, columnConfig) => {
  if (!Array.isArray(rows) || !Array.isArray(columnConfig)) return rows;
  const pendingColumnIds = columnConfig
    .filter((column) => RefreshStatus.includes(column?.status))
    .map((column) => column?.id);
  if (pendingColumnIds.length === 0) return rows;

  return rows.map((row) => {
    let nextRow = row;
    for (const columnId of pendingColumnIds) {
      const cell = row?.[columnId];
      const cellValue = cell?.cell_value ?? cell?.cellValue;
      const hasValue =
        cellValue !== undefined && cellValue !== null && cellValue !== "";
      const cellStatus = cell?.status?.toLowerCase?.();
      if (
        hasValue ||
        cellStatus === StatusTypes.RUNNING ||
        cellStatus === StatusTypes.ERROR
      ) {
        continue;
      }
      if (nextRow === row) nextRow = { ...row };
      nextRow[columnId] = { ...(cell ?? {}), status: StatusTypes.RUNNING };
    }
    return nextRow;
  });
};

const DEFAULT_MIN_WIDTH = 300;

const NonEditableColumns = [
  "run_prompt",
  "evaluation",
  "optimization",
  "annotation_label",
  "retrieval",
  "extracted_entities",
  "extracted_json",
  "python_code",
  "classification",
  "api_call",
  "conditional",
  "evaluation_reason",
];

export const getTypeDefinitions = () => {
  return {
    json: {
      baseDataType: "object",
      valueParser: (params) => {
        try {
          return JSON.parse(params.newValue);
        } catch {
          return params.newValue;
        }
      },
      valueFormatter: (params) => {
        try {
          return JSON.stringify(params.value, null, 2);
        } catch {
          return params.value;
        }
      },
    },
  };
};
export const parseDate = (value) => {
  if (!value) return null;
  if (value instanceof Date) return isNaN(value.getTime()) ? null : value;

  const str = String(value).trim();

  // Month name mapping
  const monthNames = {
    jan: 0,
    january: 0,
    feb: 1,
    february: 1,
    mar: 2,
    march: 2,
    apr: 3,
    april: 3,
    may: 4,
    jun: 5,
    june: 5,
    jul: 6,
    july: 6,
    aug: 7,
    august: 7,
    sep: 8,
    september: 8,
    oct: 9,
    october: 9,
    nov: 10,
    november: 10,
    dec: 11,
    december: 11,
  };

  let match;

  // Helper: Parse time component if present
  const parseTime = (timeStr) => {
    if (!timeStr) return { hh: 0, mm: 0, ss: 0 };
    const timeParts = timeStr.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/);
    if (!timeParts) return { hh: 0, mm: 0, ss: 0 };
    return {
      hh: parseInt(timeParts[1], 10),
      mm: parseInt(timeParts[2], 10),
      ss: timeParts[3] ? parseInt(timeParts[3], 10) : 0,
    };
  };

  // ISO format: YYYY-MM-DD with optional time
  match = str.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s](.+))?$/);
  if (match) {
    const [, y, m, d, timeStr] = match;
    const { hh, mm, ss } = parseTime(timeStr);
    return new Date(y, m - 1, d, hh, mm, ss);
  }

  // YYYY.MM.DD with optional time
  match = str.match(/^(\d{4})\.(\d{2})\.(\d{2})(?:\s+(.+))?$/);
  if (match) {
    const [, y, m, d, timeStr] = match;
    const { hh, mm, ss } = parseTime(timeStr);
    return new Date(y, m - 1, d, hh, mm, ss);
  }

  // YYYYMMDD format (compact) - no time support for this format
  match = str.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (match) {
    const [, y, m, d] = match;
    return new Date(y, m - 1, d);
  }

  // DD-MM-YYYY or MM-DD-YYYY with optional time
  match = str.match(/^(\d{1,2})-(\d{1,2})-(\d{4})(?:\s+(.+))?$/);
  if (match) {
    const [, part1, part2, year, timeStr] = match;
    const { hh, mm, ss } = parseTime(timeStr);
    const num1 = parseInt(part1, 10);
    const num2 = parseInt(part2, 10);

    if (num1 > 12) return new Date(year, num2 - 1, num1, hh, mm, ss);
    if (num2 > 12) return new Date(year, num1 - 1, num2, hh, mm, ss);
    return new Date(year, num2 - 1, num1, hh, mm, ss);
  }

  // DD.MM.YYYY or MM.DD.YYYY with optional time
  match = str.match(/^(\d{1,2})\.(\d{1,2})\.(\d{2,4})(?:\s+(.+))?$/);
  if (match) {
    const [, part1, part2, year, timeStr] = match;
    const fullYear =
      year.length === 2
        ? parseInt(year) > 50
          ? `19${year}`
          : `20${year}`
        : year;
    const { hh, mm, ss } = parseTime(timeStr);
    const num1 = parseInt(part1, 10);
    const num2 = parseInt(part2, 10);

    if (num1 > 12) return new Date(fullYear, num2 - 1, num1, hh, mm, ss);
    if (num2 > 12) return new Date(fullYear, num1 - 1, num2, hh, mm, ss);
    return new Date(fullYear, num2 - 1, num1, hh, mm, ss);
  }

  // DD/MM/YYYY or MM/DD/YYYY with optional time
  match = str.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})(?:\s+(.+))?$/);
  if (match) {
    const [, part1, part2, year, timeStr] = match;
    const fullYear =
      year.length === 2
        ? parseInt(year) > 50
          ? `19${year}`
          : `20${year}`
        : year;
    const { hh, mm, ss } = parseTime(timeStr);
    const num1 = parseInt(part1, 10);
    const num2 = parseInt(part2, 10);

    if (num1 > 12) return new Date(fullYear, num2 - 1, num1, hh, mm, ss);
    if (num2 > 12) return new Date(fullYear, num1 - 1, num2, hh, mm, ss);
    return new Date(fullYear, num2 - 1, num1, hh, mm, ss);
  }

  // DD-MM-YY with optional time
  match = str.match(/^(\d{1,2})-(\d{1,2})-(\d{2})(?:\s+(.+))?$/);
  if (match) {
    const [, part1, part2, year, timeStr] = match;
    const fullYear = parseInt(year) > 50 ? `19${year}` : `20${year}`;
    const { hh, mm, ss } = parseTime(timeStr);
    const num1 = parseInt(part1, 10);
    const num2 = parseInt(part2, 10);

    if (num1 > 12) return new Date(fullYear, num2 - 1, num1, hh, mm, ss);
    if (num2 > 12) return new Date(fullYear, num1 - 1, num2, hh, mm, ss);
    return new Date(fullYear, num2 - 1, num1, hh, mm, ss);
  }

  // DDth/st/nd/rd MonthName YYYY (e.g., "22th November 1979", "11th August 1989")
  match = str.match(/^(\d{1,2})(?:st|nd|rd|th)\s+([a-z]+)\s+(\d{4})$/i);
  if (match) {
    const [, day, monthName, year] = match;
    const month = monthNames[monthName.toLowerCase()];
    if (month !== undefined) {
      return new Date(year, month, day);
    }
  }

  // DD-MonthName-YYYY (e.g., "27-Jul-1978", "02-Sep-1997")
  match = str.match(/^(\d{1,2})-([a-z]{3,})-(\d{4})$/i);
  if (match) {
    const [, day, monthName, year] = match;
    const month = monthNames[monthName.toLowerCase()];
    if (month !== undefined) {
      return new Date(year, month, day);
    }
  }

  // DD MonthName YYYY (e.g., "12 November 1990", "30 April 1963")
  match = str.match(/^(\d{1,2})\s+([a-z]+)\s+(\d{4})$/i);
  if (match) {
    const [, day, monthName, year] = match;
    const month = monthNames[monthName.toLowerCase()];
    if (month !== undefined) {
      return new Date(year, month, day);
    }
  }

  // Try native Date.parse as fallback
  const parsed = Date.parse(str);
  if (!isNaN(parsed)) return new Date(parsed);

  // Last resort: new Date() constructor
  const date = new Date(str);
  return isNaN(date.getTime()) ? null : date;
};

const onConfigureDynamicColumn = (column) => {
  if (column.originType === "api_call") {
    useAddColumnApiCallStore.setState({
      openAddColumnApiCall: {
        editId: column.id,
      },
    });
  } else if (column.originType === "vector_db") {
    useRetrievalStore.setState({
      openRetrieval: {
        editId: column.id,
      },
    });
  } else if (column.originType === "conditional") {
    useConditionalNodeStore.setState({
      openConditionalNode: {
        editId: column.id,
      },
    });
  } else if (column.originType === "extracted_entities") {
    useExtractEntitiesStore.setState({
      openExtractEntities: {
        editId: column.id,
      },
    });
  } else if (column.originType === "extracted_json") {
    useExtractJsonKeyStore.setState({
      openExtractJsonKey: {
        editId: column.id,
      },
    });
  } else if (column.originType === "python_code") {
    useExecuteCodeStore.setState({
      openExecuteCode: {
        editId: column.id,
      },
    });
  } else if (column.originType === "classification") {
    useClassificationStore.setState({
      openClassification: {
        editId: column.id,
      },
    });
  }
};

export const getColumnConfig = ({
  eachCol,
  children,
  queryClient,
  dataset,
  getWaveSurferInstance,
  storeWaveSurferInstance,
  removeWaveSurferInstance,
  updateWaveSurferInstance,
  isViewerRole = false,
}) => {
  const setFeedBack =
    useAddEvaluationFeebackStore.getState().setAddEvaluationFeeback;

  const setImprovement = useImprovePromptStore.getState().setImprovePrompt;

  const onCellValueChanged = onCellValueChangedWrapper(queryClient, dataset);

  const editCell = useEditCellStore.getState().editCell;
  const setEditCell = useEditCellStore.getState().setEditCell;

  const colDataType = eachCol?.data_type;
  const colOriginType = eachCol?.origin_type;
  const colIsFrozen = eachCol?.is_frozen;
  const colIsVisible = eachCol?.is_visible;

  const isEditable =
    !isViewerRole &&
    !NonEditableColumns.includes(colOriginType) &&
    !["audio", "persona"].includes(colDataType);

  const baseConfig = {
    field: eachCol?.id,
    headerName: eachCol?.name,
    valueGetter: (v) => {
      const cell = v?.data?.[eachCol?.id];
      const rawValue = cell?.cell_value;
      return parseCellValue(rawValue, AGGridCellDataType[colDataType]);
    },
    valueSetter: (params) => {
      const cell = params.data[eachCol?.id];
      if (cell && "cell_value" in cell) {
        cell.cell_value = params?.newValue;
      } else if (cell) {
        cell.cellValue = params?.newValue;
      }
      return true;
    },
    editable: isEditable,
    cellDataType: AGGridCellDataType[colDataType],
    dataType: colDataType,
    pinned: colIsFrozen,
    hide: !colIsVisible,
    minWidth: DEFAULT_MIN_WIDTH,
    // suppressSizeToFit: true,
    originType: colOriginType,
    headerComponent: CustomDevelopDetailColumn,
    headerComponentParams: {
      col: eachCol,
    },
    // equals: (a, b) => a?.cellValue === b?.cellValue,
    col: {
      ...eachCol,
      // Ensure camelCase aliases exist on the plain-object copy, because
      // downstream cell renderers read `col.dataType` / `col.originType`.
      dataType: colDataType,
      originType: colOriginType,
      isFrozen: colIsFrozen,
      isVisible: colIsVisible,
      feedBackClick: setFeedBack,
      improvementClick: setImprovement,
      isHoverButtonVisible: true,
      getWaveSurferInstance: getWaveSurferInstance,
      storeWaveSurferInstance: storeWaveSurferInstance,
      removeWaveSurferInstance: removeWaveSurferInstance,
      updateWaveSurferInstance: updateWaveSurferInstance,
    },
    cellEditor:
      colDataType === "integer"
        ? "agNumberCellEditor"
        : colDataType === "text"
          ? "agLargeTextCellEditor"
          : colDataType === "boolean"
            ? "agRichSelectCellEditor"
            : colDataType === "datetime"
              ? "agDateCellEditor"
              : colDataType === "json"
                ? "JsonCellEditor"
                : "agLargeTextCellEditor",
    cellEditorParams: {
      maxLength: 100000,
      values: colDataType === "boolean" ? ["true", "false"] : undefined,
      onCellValueChanged,
    },
    cellEditorPopup: true,
    mainMenuItems: getMainMenuItems("test", isViewerRole),
    cellRenderer: CustomCellRender,
    cellRendererParams: {
      onEditCell: (params) => {
        setEditCell(params);
      },
      onCellValueChanged,
      editCell,
      editable: isEditable,
    },
    children,
    headerGroupComponent: CustomDevelopGroupCellHeader,
    headerGroupComponentParams: {
      col: eachCol,
    },
    headerClass: "develop-data-group-header",
  };

  if (colDataType === "datetime") {
    return {
      ...baseConfig,
      valueGetter: (v) => {
        const cell = v.data?.[eachCol.id];
        const rawValue = cell?.cell_value;
        const date = parseDate(rawValue);
        return date;
      },
      valueSetter: (params) => {
        const date = parseDate(params.newValue);

        if (!date && params.newValue) {
          return false;
        }

        const cell = params.data[eachCol.id];
        if (cell && "cell_value" in cell) {
          cell.cell_value = date;
        } else if (cell) {
          cell.cellValue = date;
        }
        return true;
      },
    };
  }
  return baseConfig;
};

const menuOrder = [
  "Show Reasoning",
  "Hide Reasoning",
  "Edit Eval",
  "Configure Run",
  "Configure Dynamic Column",
  "Edit Column Name",
  "Edit Column Type",
  "Delete Column",
  "separator",
  "Pin Column",
  "Sort Ascending",
  "Sort Descending",
  "separator",
  // "Choose Columns",
  "Autosize This Column",
  "Autosize All Columns",
  "Reset Columns",
];

const DynamicColumnOriginTypes = [
  "vector_db",
  "extracted_entities",
  "extracted_json",
  "python_code",
  "classification",
  "api_call",
  "conditional",
];

const getMainMenuItems =
  (currentDataset, isViewerRole = false) =>
  (params) => {
    const allMenuItems = setMenuIcons(params, currentDataset?.name); // Pass dataset name
    const menuItems = allMenuItems.slice(0);
    // const menuItems = params.defaultItems.slice(0);
    const column = params.column.colDef.col;
    const extraMenuItems = [];
    if (column?.originType === "evaluation") {
      extraMenuItems.push({
        name: "Edit Eval",
        action: () => {
          // Open the new EvaluationDrawer pre-seeded to edit this user-eval.
          // EvaluationDrawer watches pendingEditEvalId and, once the saved-
          // evals list is loaded, routes straight into the EvalPicker at the
          // config step (same as clicking Edit on the saved-evals row).
          useRunEvaluationStore
            .getState()
            .openEditEvalFromColumn(column?.sourceId);
        },
        icon: menuIcons["Configure Eval"],
      });
    }
    if (column?.originType === "run_prompt") {
      extraMenuItems.push({
        name: "Configure Run",
        action: () => {
          useRunPromptStore.getState().setOpenRunPrompt(column);
        },
        icon: menuIcons["Configure Run"],
      });
    }
    if (DynamicColumnOriginTypes.includes(column?.originType)) {
      extraMenuItems.push({
        name: "Configure Dynamic Column",
        action: () => {
          onConfigureDynamicColumn(column);
        },
        icon: menuIcons["Configure Run"],
      });
    }
    if (
      column?.originType === "evaluation" ||
      column?.originType === "evaluation_reason"
    ) {
      const { showSummary, toggleSummary } = useShowSummaryStore.getState();
      // Key the toggle by sourceId — when the reasoning children are
      // expanded, AG Grid splits the column into a group whose headers
      // use the underlying eval / eval_reason column ids, so the old
      // `column.id` key no longer matched what was stored and the
      // "Hide Reasoning" state was lost.
      const key = column?.sourceId || column?.id;
      extraMenuItems.push({
        name: showSummary.includes(key) ? "Hide Reasoning" : "Show Reasoning",
        action: () => {
          toggleSummary({ id: key });
        },
        icon: menuIcons["Show Reasoning"],
      });
    }
    if (!isViewerRole) {
      // "Edit Column Name" doesn't make sense for evaluation columns — the
      // name is derived from the eval template and is changed by editing
      // the eval itself.
      if (column?.originType !== "evaluation") {
        extraMenuItems.push({
          name: "Edit Column Name",
          action: () => {
            useEditColumnNameStore.setState({
              editColumnName: column,
            });
          },
          icon: menuIcons["Edit Column Name"],
        });
      }
      if (
        column?.originType !== "evaluation" &&
        !DevelopDataBlockedChangeDataType.includes(column?.originType)
      ) {
        extraMenuItems.push({
          name: "Edit Column Type",
          action: () => {
            useEditColumnTypeStore.setState({
              editColumnType: column,
            });
          },
          icon: menuIcons["Edit Column Type"],
        });
      }
      extraMenuItems.push({
        name: "Delete Column",
        action: () => {
          useDeleteColumnStore.setState({
            deleteColumn: column,
          });
        },
        icon: menuIcons["Delete Column"],
      });
    }
    const mainMenuItems = [...extraMenuItems, ...menuItems];
    const separatorAfter = [
      "Show Reasoning",
      "Delete Column",
      "Sort Descending",
    ];
    return reorderMenuList(mainMenuItems, menuOrder, separatorAfter);
  };

export const onCellValueChangedWrapper = (queryClient, dataset) => (params) => {
  const handlerOnSuccess = params?.onSuccess;

  const updateCellValue = async (payload, onSuccess, onError) => {
    try {
      await axios.post(endpoints.develop.updateCellValue(dataset), payload);
      onSuccess?.();
      if (typeof handlerOnSuccess === "function") {
        handlerOnSuccess();
      }
      // Invalidate JSON schema query when images are updated (to refresh maxImagesCount)
      if (dataType === "images") {
        queryClient.invalidateQueries({
          queryKey: ["json-column-schema", dataset],
        });
      }
    } catch (e) {
      logger.error("Failed to update cell:", e);
      onError?.();
      enqueueSnackbar(
        "Failed to update cell value. Reverting to previous value.",
        {
          variant: "error",
        },
      );
    }
  };

  if (params?.type === "cellValueChanged" && params?.source === undefined) {
    return;
  }

  const columnId = params?.column?.colId;
  const rowId = params?.data?.row_id ?? params?.data?.rowId;
  const newValue = params?.newValue;
  const oldValue = params?.oldValue;
  const dataType = params?.column?.colDef?.dataType;
  const _fileName = params?.fileName;

  const gridApi = params.api;
  const rowNode = gridApi.getRowNode(rowId);

  if (rowNode) {
    try {
      if (newValue instanceof File) {
        const formData = new FormData();
        formData.append("column_id", columnId);
        formData.append("row_id", rowId);
        formData.append("new_value", newValue);
        updateCellValue(
          formData,
          () => {
            const tempUrl = URL.createObjectURL(newValue);
            rowNode.setDataValue(columnId, tempUrl);
          },
          () => rowNode.setDataValue(columnId, oldValue),
        );
      } else if (
        typeof newValue === "string" &&
        newValue.startsWith("data:image/")
      ) {
        updateCellValue(
          { column_id: columnId, row_id: rowId, new_value: newValue },
          () => rowNode.setDataValue(columnId, newValue),
          () => rowNode.setDataValue(columnId, oldValue),
        );
      } else if (dataType === "datetime") {
        const date = new Date(newValue);
        const formattedDate = format(date, "yyyy-MM-dd HH:mm:ss");

        updateCellValue(
          { column_id: columnId, row_id: rowId, new_value: formattedDate },
          () => rowNode.setDataValue(columnId, date),
          () => rowNode.setDataValue(columnId, oldValue),
        );
      } else if (dataType === "document") {
        updateCellValue(
          { column_id: columnId, row_id: rowId, new_value: newValue },
          () => {
            gridApi?.refreshServerSide({});
          },
          () => rowNode.setDataValue(columnId, oldValue),
        );
      } else {
        const formattedValue =
          typeof newValue === "object" && newValue !== null
            ? JSON.stringify(newValue)
            : newValue?.toString() ?? "";

        updateCellValue(
          { column_id: columnId, row_id: rowId, new_value: formattedValue },
          () => rowNode.setDataValue(columnId, formattedValue),
          () => rowNode.setDataValue(columnId, oldValue),
        );
      }
    } catch (e) {
      logger.warn("Warning:", e);
      rowNode.setDataValue(columnId, oldValue);
      enqueueSnackbar(
        "An unexpected error occurred. Reverting to previous value.",
        {
          variant: "error",
        },
      );
    }
  }
};

export const getStatusColor = (value, theme) => {
  if (!value) {
    return {
      backgroundColor: interpolateColorBasedOnScore(0, 1),
      color: theme.palette.red[500],
      fontWeight: 400,
    };
  }

  if (Array.isArray(value)) {
    return {
      backgroundColor: theme.palette.action.hover,
      color: theme.palette.primary.main,
      fontWeight: 400,
    };
  }

  if (value === STATUS.PASSED) {
    return {
      backgroundColor: interpolateColorBasedOnScore(1, 1),
      color: theme.palette.green[500],
      fontWeight: 400,
    };
  }

  // if (typeof value === "number") {
  //   return {
  //     backgroundColor: interpolateColorBasedOnScore(value, 1),
  //     color: value >= 0.5 ? theme.palette.green[500] : theme.palette.red[500],
  //     fontWeight: 400,
  //   };
  // }

  if (!isNaN(Number(value))) {
    const numericValue = Number(value) * 100;

    let color, backgroundColor;

    if (numericValue <= 49) {
      color = "red.500";
      backgroundColor = "red.o10";
    } else if (numericValue <= 79) {
      color = "orange.500";
      backgroundColor = "orange.o10";
    } else if (numericValue <= 100) {
      color = "green.500";
      backgroundColor = "green.o10";
    } else {
      color = "green.500";
      backgroundColor = "green.o10";
    }

    return {
      color,
      backgroundColor,
      fontWeight: 400,
    };
  }
  // >>>>>>> dev

  if (value === STATUS.FAILED) {
    return {
      backgroundColor: interpolateColorBasedOnScore(0, 1),
      color: theme.palette.red[500],
      fontWeight: 400,
    };
  }

  return {
    backgroundColor: theme.palette.action.hover,
    color: theme.palette.primary.main,
    fontWeight: 400,
  };
};

export const parsePythonReprIfNeeded = (value) => {
  if (typeof value !== "string") return value;
  const isDict = value.startsWith("{") && value.endsWith("}");
  const isList = value.startsWith("[") && value.endsWith("]");
  if (!isDict && !isList) return value;
  try {
    return JSON.parse(value.replace(/'/g, '"'));
  } catch {
    return value;
  }
};

const extractChoiceArray = (obj) => {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
  if (Array.isArray(obj.choices)) return obj.choices;
  if (Array.isArray(obj.choice)) return obj.choice;
  return null;
};

// Display label for a choice-shaped value ({ choice } / { choices });
// joins arrays, returns null when the value isn't choice-shaped.
export const extractChoiceLabel = (/** @type {any} */ obj) => {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
  if (Array.isArray(obj.choices)) return obj.choices.join(", ");
  if (Array.isArray(obj.choice)) return obj.choice.join(", ");
  if (typeof obj.choices === "string") return obj.choices;
  if (typeof obj.choice === "string") return obj.choice;
  return null;
};

// Numeric score from an already-normalized eval value.
export const extractScore = (/** @type {any} */ normalized) => {
  if (typeof normalized === "number") return normalized;
  if (
    normalized &&
    typeof normalized === "object" &&
    typeof normalized.score === "number"
  ) {
    return normalized.score;
  }
  return parseFloat(normalized);
};

export const normalizeEvalCellValue = (value) => {
  const v = parsePythonReprIfNeeded(value);
  if (Array.isArray(v)) return v;
  const arr = extractChoiceArray(v);
  if (arr) return arr;
  return v;
};

export const cleanChoiceLabel = (value) => {
  const parsed = parsePythonReprIfNeeded(value);
  if (Array.isArray(parsed)) return parsed.map((v) => String(v)).join(", ");
  return String(parsed ?? value);
};

// Map an outputType string (with all its casing/spelling variants) to a canonical
// kind. Returns null when no type was given or it isn't recognized.
const canonicalKindFromOutputType = (/** @type {any} */ outputType) => {
  const t = String(outputType || "").toLowerCase();
  if (!t) return null;
  if (t === "pass/fail" || t === "pass_fail") return "passfail";
  if (t === "choices" || t === "choice") return "choices";
  if (t === "score" || t === "percentage" || t === "numeric") return "score";
  return null;
};

// Infer the kind from the value's shape alone, used when no outputType is known.
const inferKindFromValue = (/** @type {any} */ v) => {
  if (typeof v === "number") return "score";
  if (typeof v === "string") {
    const trimmed = v.trim();
    const lowered = trimmed.toLowerCase();
    if (
      lowered === "passed" ||
      lowered === "failed" ||
      lowered === "pass" ||
      lowered === "fail"
    ) {
      return "passfail";
    }
    // numeric-looking string → score
    if (trimmed !== "" && !Number.isNaN(parseFloat(trimmed))) return "score";
    // any other string is treated as a single choice label
    return "choices";
  }
  if (Array.isArray(v)) return "choices";
  if (v && typeof v === "object") {
    if (extractChoiceArray(v) || extractChoiceLabel(v)) return "choices";
    if (typeof v.score === "number") return "score";
  }
  return null;
};

/**
 * @param {any} value
 * @param {string} [outputType] - optional; when omitted, the kind is inferred from the value shape
 */
export const normalizeEvalResult = (value, outputType) => {
  const v = parsePythonReprIfNeeded(value);

  if (
    v === null ||
    v === undefined ||
    v === "" ||
    (Array.isArray(v) && v.length === 0)
  ) {
    return { kind: "empty" };
  }

  let kind = canonicalKindFromOutputType(outputType) ?? inferKindFromValue(v);

  // If the value clearly carries choice information (e.g. {score, choice: "x"}
  // or {score, choices: [...]}), prefer rendering as choices even when the
  // declared output_type was "score". Backend evals with choice_scores
  // configured produce this hybrid shape while still reporting output: "score".
  const hasChoiceShape =
    v &&
    typeof v === "object" &&
    !Array.isArray(v) &&
    (extractChoiceArray(v) || extractChoiceLabel(v));
  if (hasChoiceShape) kind = "choices";

  if (kind === "passfail") {
    const items = Array.isArray(v) ? v : [v];
    const label = items.map((x) => String(x ?? "")).join(", ");
    const passed = !label.toLowerCase().includes("fail") && !!label;
    return { kind: "passfail", label, pass: passed };
  }

  if (kind === "choices") {
    let items;
    if (Array.isArray(v)) items = v;
    else if (v && typeof v === "object") {
      items = extractChoiceArray(v) ?? [extractChoiceLabel(v) ?? ""];
    } else {
      items = [v];
    }
    items = items
      .map((/** @type {any} */ x) =>
        x && typeof x === "object" ? x.choice ?? x.label ?? x.value ?? "" : x,
      )
      .map((/** @type {any} */ x) => String(x ?? ""))
      .filter(Boolean);
    if (items.length === 0) return { kind: "empty" };
    const score =
      v &&
      typeof v === "object" &&
      !Array.isArray(v) &&
      typeof v.score === "number"
        ? v.score
        : null;
    return { kind: "choices", items, score };
  }

  if (kind === "score") {
    const num =
      typeof v === "number"
        ? v
        : v && typeof v === "object" && typeof v.score === "number"
          ? v.score
          : parseFloat(v);
    if (Number.isNaN(num)) return { kind: "empty" };
    return { kind: "score", score: num };
  }

  // Couldn't classify — fall back to a displayable label.
  if (typeof v === "string" || typeof v === "number") {
    return { kind: "passfail", label: String(v), pass: null };
  }
  return { kind: "empty" };
};

export const getLabel = (value) => {
  const v = parsePythonReprIfNeeded(value);
  if (Array.isArray(v) && v[0]) return v[0];

  if (v && typeof v === "object") {
    const arr = extractChoiceArray(v);
    if (arr && arr[0]) return arr[0];
    const choiceStr = extractChoiceLabel(v);
    if (choiceStr) return choiceStr;
    if (typeof v.score === "number") {
      return `${(v.score * 100).toFixed(0)}%`;
    }
    return "";
  }
  const numericValue = parseFloat(v);
  if (!isNaN(numericValue)) {
    return `${(numericValue * 100).toFixed(0)}%`;
  }
  return v;
};

export const DATASET_TYPES = {
  SYNTHETIC_DATASET: "synthetic_dataset",
};

export const enhanceCol = (col, averageMetaData) => {
  const columnConfig = averageMetaData?.find((d) => d.id === col.id);
  if (!columnConfig) return col;
  return {
    ...col,
    metadata: columnConfig?.metadata,
    data_type: col?.data_type,
    dataType: col?.data_type,
    origin_type: col?.origin_type,
    originType: col?.origin_type,
    is_frozen: col?.is_frozen,
    isFrozen: col?.is_frozen,
    is_visible: col?.is_visible,
    isVisible: col?.is_visible,
    source_id: col?.source_id,
    sourceId: col?.source_id,
  };
};

export const DUMMY_ROWS = [
  {
    rowId: 1,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
  {
    rowId: 2,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
  {
    rowId: 3,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
  {
    rowId: 4,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
  {
    rowId: 5,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
  {
    rowId: 6,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
  {
    rowId: 7,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
  {
    rowId: 8,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
  {
    rowId: 9,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
  {
    rowId: 10,
    1: { status: StatusTypes.RUNNING },
    2: { status: StatusTypes.RUNNING },
    3: { status: StatusTypes.RUNNING },
    4: { status: StatusTypes.RUNNING },
  },
];
export const getDatasetViewOptions = (viewOptions) => {
  return {
    showCheckbox:
      viewOptions?.showCheckbox !== undefined
        ? viewOptions?.showCheckbox
        : true,
    showDrawer:
      viewOptions?.showDrawer !== undefined ? viewOptions?.showDrawer : true,
    bottomRow:
      viewOptions?.bottomRow !== undefined ? viewOptions?.bottomRow : true,
    showEvals:
      viewOptions?.showEvals !== undefined ? viewOptions?.showEvals : true,
  };
};

export const postProcessPopup = (params) => {
  if (params.type !== "columnMenu") {
    return;
  }

  const ePopup = params.ePopup;
  ePopup.style.backgroundColor = "var(--bg-paper, #fff)";
  ePopup.style.borderRadius = "12px";
  ePopup.style.border = "1px solid var(--border-default, #e5e7eb)";
  ePopup.style.padding = "16px";
  ePopup.style.margin = "0px";
  ePopup.style.boxShadow = "0px 4px 10px rgba(0, 0, 0, 0.1)";
  ePopup.style.fontFamily = "Inter, sans-serif";
  ePopup.style.fontWeight = 400;
  ePopup.style.color = "var(--text-primary)";

  const menuItemsList = ePopup.querySelectorAll(".ag-menu-list");
  menuItemsList.forEach((item) => {
    item.style.padding = "0px";
  });

  const menuItems = ePopup.querySelectorAll(".ag-menu-option");
  menuItems.forEach((item) => {
    item.style.height = "30px";
    item.style.minHeight = "30px";
    item.style.padding = "0px";
    item.style.borderRadius = "4px";
    item.style.transition = "background-color 0.15s";
    item.addEventListener("mouseenter", () => {
      item.style.backgroundColor = "var(--surface-row-hover, rgba(0,0,0,0.04))";
    });
    item.addEventListener("mouseleave", () => {
      item.style.backgroundColor = "transparent";
    });
  });

  const menuParts = ePopup.querySelectorAll(".ag-menu-option-part");
  menuParts.forEach((part) => {
    part.style.padding = "0px 12px";
    part.style.margin = "0";
    part.style.lineHeight = "normal";
  });

  const elements = ePopup.querySelectorAll('span[data-ref="eName"]');
  elements.forEach((element) => {
    if (
      element.textContent.trim() === "Show Reasoning" ||
      element.textContent.trim() === "Hide Reasoning"
    ) {
      element.style.color = "var(--primary-main)";
    }
    if (element.textContent.trim() === "Delete Column") {
      element.style.color = "#fa0c0c";
    }
    element.style.fontWeight = 400;
    element.style.fontSize = "12px";
  });

  const separatorLines = ePopup.querySelectorAll(".ag-menu-separator-part");
  separatorLines.forEach((line) => {
    line.style.height = "1px";
  });

  const icons = ePopup.querySelectorAll(".ag-menu-option-icon");
  icons.forEach((icon) => {
    icon.style.padding = "0";
  });
};
