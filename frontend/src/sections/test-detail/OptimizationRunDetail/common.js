import { KeyOptimizerMapping } from "../CreateEditOptimization/common";
import OptimizationNameRenderer from "./CellRenderers/OptimizationNameRenderer";
import StatusCellRenderer from "./CellRenderers/StatusCellRenderer";

export const getOptimizationRunDetailColumDef = () => {
  return [
    {
      field: "optimizations",
      headerName: "Optimizations",
      valueGetter: (params) => ({
        title: params.data?.optimisation_name ?? params.data?.optimisationName,
        startedAt: params.data?.started_at ?? params.data?.startedAt,
      }),
      flex: 1,
      cellRenderer: OptimizationNameRenderer,
    },
    {
      field: "noOfTrials",
      headerName: "No. of Trials",
      valueGetter: (params) =>
        params.data?.no_of_trials ?? params.data?.noOfTrials,
      minWidth: 150,
    },
    {
      field: "optimizationType",
      headerName: "Optimization Type",
      valueGetter: (params) =>
        KeyOptimizerMapping[
          params.data?.optimiser_type ?? params.data?.optimiserType
        ] ??
        params.data?.optimiser_type ??
        params.data?.optimiserType,
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
