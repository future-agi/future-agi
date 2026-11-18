import { format } from "date-fns";
import ExecutionStatusCellRenderer from "./CellRenderers/ExecutionStatusCellRenderer";

export const getSimulationExecutionsColDef = () => {
  return [
    {
      headerName: "Scenario",
      field: "scenarios",
      flex: 1,
      minWidth: 150,
    },
    {
      headerName: "Run Start Time",
      field: "start_time",
      flex: 1,
      minWidth: 150,
      valueFormatter: (params) => {
        if (!params.value) return "-";
        return format(new Date(params.value), "yyyy-MM-dd HH:mm");
      },
    },
    {
      headerName: "Total Chats",
      flex: 1,
      minWidth: 100,
      field: "total_chats",
      valueFormatter: (params) => params.value ?? 0,
    },
    {
      headerName: "Run Status",
      field: "status",
      flex: 1,
      minWidth: 180,
      cellRenderer: ExecutionStatusCellRenderer,
    },
    {
      headerName: "Total Turns",
      field: "total_number_of_fagi_agent_turns",
      flex: 1,
      minWidth: 100,
      valueFormatter: (params) => {
        if (!params.value) return "-";
        return params?.value;
      },
    },
    {
      headerName: "% Chats Completed",
      flex: 1,
      minWidth: 110,
      field: "success_rate",
      valueFormatter: (params) => {
        if (params.value == null) return "-";
        return `${params.value}%`;
      },
    },
  ];
};

// Helper hook to get selected count from grid
export const getSelectedCount = (
  gridApi,
  toggledNodes,
  selectAll,
  totalRowCount,
) => {
  if (!gridApi) return 0;

  if (selectAll) {
    return totalRowCount - toggledNodes.length;
  } else {
    return toggledNodes.length;
  }
};
export const DRAWER_OPEN_ENUMS = {
  EVALS: "evals",
  DELETE: "delete",
};
