import ExperimentHeader from "./CustomRenderers/ExperimentHeader";
import ExperimentNameCellRenderer from "./CustomRenderers/ExperimentNameCellRenderer";

export const ExperimentSummaryDefaultColDef = {
  lockVisible: true,
  sortable: true,
  filter: false,
  resizable: true,
  editable: false,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
  headerComponent: ExperimentHeader,
};
const commonCellStyle = {
  display: "flex",
  justifyContent: "center",
  alignItems: "center",
};

export const ExperimentSummaryStaticColumnDefs = [
  {
    headerName: "Experiment Name",
    // colId keeps the header icon mapping in ExperimentHeader.jsx.
    colId: "experimentDatasetName",
    field: "experiment_dataset_name",
    cellRenderer: ExperimentNameCellRenderer,
    cellStyle: {
      overflow: "auto",
    },
    flex: 1,
    minWidth: 300,
  },
  {
    headerName: "Average Response time (ms)",
    colId: "averageResponseTime",
    field: "average_response_time",
    flex: 1,
    cellStyle: {
      ...commonCellStyle,
    },
    valueFormatter: (params) => {
      return params?.value !== undefined
        ? `${parseFloat(params?.value.toFixed(1))} ms`
        : 0;
    },
    minWidth: 200,
  },
  {
    headerName: "Prompt Tokens",
    colId: "promptToken",
    field: "prompt_token",
    flex: 1,
    cellStyle: {
      ...commonCellStyle,
    },
    valueFormatter: (params) => {
      return params?.value !== undefined
        ? parseFloat(params?.value.toFixed(2))
        : 0;
    },
    minWidth: 200,
  },
  {
    headerName: "Completion Tokens",
    colId: "completionToken",
    field: "completion_token",
    cellStyle: {
      ...commonCellStyle,
    },
    flex: 1,
    valueFormatter: (params) => {
      return params?.value !== undefined
        ? parseFloat(params?.value.toFixed(2))
        : 0;
    },
    minWidth: 200,
  },
  {
    headerName: "Total Tokens",
    colId: "totalToken",
    field: "total_token",
    flex: 1,
    cellStyle: {
      ...commonCellStyle,
    },
    valueFormatter: (params) => {
      return params?.value !== undefined
        ? parseFloat(params?.value.toFixed(2))
        : 0;
    },
    minWidth: 200,
  },
];

export const mockSummaryData = [
  {
    experimentDatasetName: "Customer Support Bot v1",
    avgResponseTime: 245,
    promptToken: 320,
    completionToken: 180,
    totalToken: 500,
  },
  {
    experimentDatasetName: "Product Recommendations A",
    avgResponseTime: 189,
    promptToken: 280,
    completionToken: 150,
    totalToken: 430,
  },
  {
    experimentDatasetName: "FAQ Assistant Beta",
    avgResponseTime: 312,
    promptToken: 400,
    completionToken: 220,
    totalToken: 620,
  },
  {
    experimentDatasetName: "Sales Chatbot 2.0",
    avgResponseTime: 278,
    promptToken: 350,
    completionToken: 190,
    totalToken: 540,
  },
  {
    experimentDatasetName: "Support Ticket Classifier",
    avgResponseTime: 156,
    promptToken: 250,
    completionToken: 130,
    totalToken: 380,
  },
  {
    experimentDatasetName: "Email Response Generator",
    avgResponseTime: 298,
    promptToken: 420,
    completionToken: 240,
    totalToken: 660,
  },
  {
    experimentDatasetName: "Product Description Writer",
    avgResponseTime: 334,
    promptToken: 380,
    completionToken: 210,
    totalToken: 590,
  },
  {
    experimentDatasetName: "Customer Feedback Analyzer",
    avgResponseTime: 223,
    promptToken: 290,
    completionToken: 160,
    totalToken: 450,
  },
  {
    experimentDatasetName: "Order Processing Assistant",
    avgResponseTime: 267,
    promptToken: 310,
    completionToken: 170,
    totalToken: 480,
  },
  {
    experimentDatasetName: "Technical Support Helper",
    avgResponseTime: 289,
    promptToken: 340,
    completionToken: 200,
    totalToken: 540,
  },
];
