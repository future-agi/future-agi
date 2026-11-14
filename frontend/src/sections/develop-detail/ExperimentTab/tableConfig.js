import ConsistentCellRender from "../../../components/CommonCellRenderer/ConsistentCellRender";
import TableAction from "../../../components/TableAction/TableAction";
import customColHeaderRenderer from "./customColHeaderRenderer";
import { dateValueFormatter } from "src/utils/dateTimeUtils";

// Column Definitions
export const ExperimentTabColumnDefs = [
  {
    checkboxSelection: true,
    headerCheckboxSelection: true,
    headerCheckboxSelectionFilteredOnly: true,
    minWidth: 30,
    columnType: "checkbox",
    filter: false,
    width: 50,
    headerComponent: null,
  },
  {
    headerName: "Experiment Name",
    field: "name",
    flex: 1.25,
    cellStyle: { fontSize: "12px", fontWeight: 500, color: "text.secondary" },
  },
  {
    headerName: "Status",
    field: "status",
    flex: 1,
    cellRenderer: ConsistentCellRender,
  },
  {
    headerName: "No.of models added",
    field: "models_count",
    flex: 1,
    cellStyle: {
      alignItems: "center",
      justifyContent: "center",
      display: "flex",
      fontSize: "12px",
      fontWeight: 400,
      color: "text.secondary",
    },
  },

  {
    headerName: "No.of Evals Added",
    field: "eval_templates_count",
    flex: 1,
    cellStyle: {
      alignItems: "center",
      justifyContent: "center",
      display: "flex",
      fontSize: "12px",
      fontWeight: 400,
      color: "text.secondary",
    },
  },
  {
    headerName: "Actions",
    field: "actions",
    flex: 1,
    cellRenderer: TableAction,
  },
  {
    headerName: "Created At",
    field: "created_at",
    flex: 1,
    valueFormatter: dateValueFormatter,
  },
];

export const ExperimentDefaultColDef = {
  lockVisible: true,
  sortable: true,
  filter: false,
  resizable: false,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
  headerComponent: customColHeaderRenderer,
};
