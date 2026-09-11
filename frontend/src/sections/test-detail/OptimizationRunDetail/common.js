import { KeyOptimizerMapping } from "../CreateEditOptimization/common";
import OptimizationNameRenderer from "./CellRenderers/OptimizationNameRenderer";
import StatusCellRenderer from "./CellRenderers/StatusCellRenderer";

export const getOptimizationRunDetailColumDef = () => {
  return [
    {
      field: "optimizations",
      headerName: "Optimizations",
      valueGetter: (params) => ({
        title: params.data?.optimisation_name,
        startedAt: params.data?.started_at,
      }),
      flex: 1,
      cellRenderer: OptimizationNameRenderer,
    },
    {
      field: "no_of_trials",
      headerName: "No. of Trials",
      minWidth: 150,
    },
    {
      field: "optimization_type",
      headerName: "Optimization Type",
      valueGetter: (params) => KeyOptimizerMapping[params.data?.optimiser_type],
      minWidth: 200,
    },
    {
      field: "status",
      headerName: "Status",
      cellRenderer: StatusCellRenderer,
      minWidth: 150,
    },
  ];
};
