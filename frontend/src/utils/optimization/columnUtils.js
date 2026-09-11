/**
 * Shared column utilities for optimization results.
 *
 * Used by both:
 * - DatasetOptimizationResultBar / DatasetOptimizationResultGrid
 * - OptimizationResultBar / OptimizationResultGrid (simulation)
 */

/**
 * Transform AG Grid column definitions to a simpler column structure array.
 *
 * @param {Array} colDefs - Array of AG Grid column definitions
 * @returns {Array} Array of simplified column structures with name from headerName
 */
export const transformColDefToColumnStructure = (colDefs) => {
  if (!colDefs) return [];
  return colDefs.reduce((acc, col) => {
    acc.push({ ...col, name: col?.headerName });
    return acc;
  }, []);
};

/**
 * Get column configuration for optimization result grid.
 *
 * Maps column config from backend to AG Grid column definitions.
 *
 * @param {Array} columnConfig - Column configuration from backend
 * @param {Object} cellRenderers - Object containing cell renderer components
 * @param {Function} cellRenderers.TrialCellRenderer - Renderer for trial column
 * @param {Function} cellRenderers.AverageEvalCellRenderer - Renderer for eval columns
 * @param {Function} cellRenderers.OptimizeResultHeader - Header component for eval columns
 * @param {Function} cellRenderers.PromptTooltip - Tooltip component for prompt column
 * @returns {Array} AG Grid column definitions
 */
export const getOptimizationResultColumnConfig = (
  columnConfig,
  cellRenderers = {},
) => {
  const {
    TrialCellRenderer,
    AverageEvalCellRenderer,
    OptimizeResultHeader,
    PromptTooltip,
  } = cellRenderers;

  if (!columnConfig || !Array.isArray(columnConfig)) return [];

  return columnConfig.map((column) => {
    switch (column?.id) {
      case "trial":
        return {
          field: "trial",
          headerName: "Trial",
          cellRenderer: TrialCellRenderer,
          minWidth: 200,
          isVisible: true,
          id: "trial",
          colId: "trial",
          valueGetter: (params) => ({
            title: params.data?.trial,
            improvement: params.data?.scorePercentageChange,
            isBest: params.data?.isBest,
          }),
        };

      case "prompt":
        return {
          field: "prompt",
          headerName: "Prompts",
          minWidth: 300,
          maxWidth: 400,
          flex: 1,
          id: "prompt",
          colId: "prompt",
          tooltipComponent: PromptTooltip,
          tooltipValueGetter: ({ data }) => data?.prompt,
          isVisible: true,
        };

      default:
        // Eval columns - data is already in { score, percentageChange } format
        return {
          field: column?.id,
          headerName: column?.name,
          minWidth: 170,
          colId: column?.id,
          cellRenderer: AverageEvalCellRenderer,
          headerComponent: OptimizeResultHeader,
          isVisible: true,
          id: column?.id,
        };
    }
  });
};

/**
 * Default AG Grid column definition settings for optimization grids.
 */
export const defaultOptimizationColDef = {
  lockVisible: true,
  sortable: false,
  filter: false,
  resizable: true,
  suppressMenu: true,
};
