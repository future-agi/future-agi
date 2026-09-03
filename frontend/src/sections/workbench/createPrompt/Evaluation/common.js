import ColumnHeaderComponent from "./ColumnHeaderComponent";
import EvaluationCellRendererWrapper from "./EvaluationCellRendererWrapper";

const DEFAULT_MIN_WIDTH = 300;

// Average character width (px) at typical UI font size; used to derive chars-per-line from cell min width
const AVG_CHAR_WIDTH_PX = 4;

// Row height calculation (used by calculateRowHeight)
const ROW_HEIGHT_BASE = 60; // Base height for padding
const ROW_HEIGHT_LINE = 20; // Height per line of text
const ROW_HEIGHT_CHAR_PER_LINE = Math.floor(
  DEFAULT_MIN_WIDTH / AVG_CHAR_WIDTH_PX,
); // ~42 at 300px min width
const ROW_HEIGHT_MIN = 70; // Minimum row height
const ROW_HEIGHT_MAX = 250; // Maximum height (~10 lines)

export const CELL_STATE = {
  LOADING: "__LOADING__",
  EMPTY: "__EMPTY__",
};

// Unsaved local row: flagged _isLocal on add (cleared once persisted). Not
// sniffing __EMPTY__ cells, which misfires after the row is run or compared.
export const isUnsavedRow = (rowData) => !!rowData?._isLocal;

export const mergeUnsavedRows = (next, prev) => {
  if (!Array.isArray(next) || !Array.isArray(prev)) return next;
  if (!next.every((row) => row?.id != null)) return next;
  const nextIds = new Set(next.map((row) => row.id));
  const unsaved = prev.filter(
    (row) => isUnsavedRow(row) && row.id != null && !nextIds.has(row.id),
  );
  return unsaved.length ? [...next, ...unsaved] : next;
};

export const COLUMNIDS = {
  COMPARISON: "Comparison",
};

const getGroupColumnConfig = (col, level) => {
  return {
    field: col.id,
    headerName: level === 1 ? "" : col.name,
    editable: false,
    hide: col.hide ? col.hide : false,
    dataType: col?.dataType ?? "text",
    originType: col?.originType ?? "text",
    headerGroupComponent: ColumnHeaderComponent,
    minWidth: col.minWidth ?? DEFAULT_MIN_WIDTH,
    autoHeaderHeight: level === 1,
    wrapHeaderText: level === 1,
    headerGroupComponentParams: {
      col: col,
      headerLevel: level,
    },
  };
};

const getChildColumnConfig = (children) => {
  const childColumnConfig = [];
  for (let i = 0; i < children.length; i++) {
    const col = children[i];
    const config = {
      field: col.id,
      headerName: col.name,
      editable: col.editable ?? false,
      hide: col.hide ? col.hide : false,
      dataType: col?.dataType ?? "text",
      originType: col?.originType ?? "text",
      headerComponent: ColumnHeaderComponent,
      minWidth: col.minWidth ?? DEFAULT_MIN_WIDTH,
      tooltipComponentParams: col.tooltipComponentParams,
      // autoHeight:true,
      // wrapText: true,
      cellRenderer: EvaluationCellRendererWrapper,
      headerComponentParams: {
        col: col,
        headerLevel: 3,
      },
      cellRendererParams: {
        col: col,
      },
    };
    childColumnConfig.push(config);
  }
  return childColumnConfig;
};

export const getColumnConfig = (col) => {
  if (col.showPrompts) {
    return {
      ...getGroupColumnConfig(col, 1),
      children: [
        getColumnConfig({
          id: col.id,
          name: col.name,
          hide: col.hide,
          template_version: col.template_version,
          dataType: col?.dataType ?? "text",
          originType: col?.originType ?? "text",
          minWidth: col.minWidth ?? DEFAULT_MIN_WIDTH,
          model_detail: col.model_detail,
          showPrompts: false,
          children: col.children ?? [col],
        }),
      ],
    };
  }
  return {
    ...getGroupColumnConfig(col, 2),
    children: getChildColumnConfig(col.children ?? [col]),
  };
};

/**
 * Calculate row height based on content string length
 * @param {string} content - The text content to measure
 * @returns {number} - Calculated row height in pixels
 */
export const calculateRowHeightForContent = (content) => {
  if (!content || typeof content !== "string") return ROW_HEIGHT_MIN;

  const estimatedLines = Math.ceil(content.length / ROW_HEIGHT_CHAR_PER_LINE);
  const calculatedHeight = ROW_HEIGHT_BASE + estimatedLines * ROW_HEIGHT_LINE;
  return Math.min(Math.max(calculatedHeight, ROW_HEIGHT_MIN), ROW_HEIGHT_MAX);
};

/**
 * Calculate row height for evaluation data based on longest output
 * @param {Object} rowData - The row data object
 * @returns {number} - Calculated row height in pixels
 */
export const calculateRowHeight = (rowData) => {
  if (!rowData) return ROW_HEIGHT_MIN;

  const outputContents = Object.keys(rowData)
    .filter((key) => key.startsWith("Output-"))
    .map((key) => {
      const val = rowData[key];
      return typeof val === "string" ? val : JSON.stringify(val) || "";
    });

  if (!outputContents.length) return ROW_HEIGHT_MIN;

  return Math.max(...outputContents.map(calculateRowHeightForContent));
};
