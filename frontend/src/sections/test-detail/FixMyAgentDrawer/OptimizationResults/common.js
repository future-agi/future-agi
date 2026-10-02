/**
 * Simulation Optimization Results - Common Utilities
 *
 * Column configuration is defined locally as it depends on local cell renderers.
 * Graph utilities are imported from shared location.
 */

import AverageEvalCellRenderer from "./CellRenderers/AverageEvalCellRenderer";
import OptimizeResultHeader from "./CellRenderers/OptimizeResultHeader";
import PromptTooltip from "./CellRenderers/PromptTooltip";
import TrialCellRenderer from "./CellRenderers/TrialCellRenderer";

// Re-export shared graph utilities for backward compatibility
export {
  getCategories,
  getGraphTooltipComponent,
  updateCrosshairColor,
} from "src/utils/optimization";

/**
 * Build column configuration for optimization result grid.
 * Uses local cell renderers specific to simulation optimization.
 */
export const getOptimizationResultColumnConfig = (optimizationColumns) => {
  if (!optimizationColumns || !Array.isArray(optimizationColumns)) return [];
  return optimizationColumns.map((column) => {
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
          valueGetter: (params) => {
            return {
              title: params.data?.trial,
              improvement: params.data?.score_percentage_change,
              isBest: params.data?.is_best,
            };
          },
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
