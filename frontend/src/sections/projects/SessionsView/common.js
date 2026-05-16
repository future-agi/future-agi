import CustomHeader from "./custom-header";
import SessionCellRenderer from "./SessionCellRenderer";
import NewAnnotationCellRenderer from "../../agents/NewAnnotationCellRenderer";

export const getSessionListColumnDef = (col) => {
  const { id, name, isVisible } = col;
  const isCustomColumn = col.groupBy === "Custom Columns";

  // Annotation Metrics columns use a specialized renderer
  if (col.groupBy === "Annotation Metrics") {
    const annotationType = col.annotationLabelType;
    const settings = col.settings || {};
    return {
      headerName: name,
      field: id,
      hide: !isVisible,
      headerComponent: CustomHeader,
      sortable: true,
      minWidth: 200,
      headerComponentParams: { dataType: "text" },
      valueGetter: (params) => {
        const metricData = params?.data?.[id];
        if (!metricData) return null;
        if (metricData.score !== undefined) return metricData.score;
        return null;
      },
      cellRenderer: NewAnnotationCellRenderer,
      cellRendererParams: {
        annotationType,
        isAverage: true,
        settings,
        originType: "Tracing",
      },
      cellStyle: {
        paddingInline: 0,
        justifyContent: "flex-end",
        display: "flex",
      },
    };
  }

  // Custom columns use valueGetter to handle dot-notation attribute keys
  if (isCustomColumn) {
    return {
      headerName: name,
      colId: id,
      hide: !isVisible,
      headerComponent: CustomHeader,
      sortable: false,
      minWidth: 180,
      flex: 1,
      headerComponentParams: { dataType: "text" },
      valueGetter: (params) => {
        if (!params.data) return null;
        let value = params.data[id];
        if (value === undefined && id.includes(".")) {
          value = id.split(".").reduce((obj, key) => obj?.[key], params.data);
        }
        if (value === undefined || value === null) return null;
        if (Array.isArray(value) || typeof value === "object") {
          return JSON.stringify(value);
        }
        return String(value);
      },
      valueFormatter: (params) =>
        params.value === null || params.value === undefined
          ? "—"
          : params.value,
      cellStyle: {
        paddingInline: 0,
        justifyContent: "flex-end",
        display: "flex",
      },
    };
  }

  return {
    headerName: name,
    field: id,
    hide: !isVisible,
    headerComponent: CustomHeader,
    sortable: true,
    minWidth: 200,
    col: { dataType: "text" },
    headerComponentParams: { dataType: "text" },
    filter: id === "duration" || id === "lastMessage" ? false : undefined,
    cellRenderer: SessionCellRenderer,
    cellStyle: {
      paddingInline: 0,
      justifyContent: "flex-end",
      display: "flex",
    },
  };
};

export const metaDataLabelMapper = {
  total_latency_ms: "Trace Latency",
  user_id: "User Id",
  total_cost: "Cost",
  start_time: "Start Time",
  total_token_count: "Total Token Count",
  input_tokens: "Input Tokens",
  output_tokens: "Output Tokens",
};

export const metadataIconMapper = {
  total_latency_ms: "/assets/icons/navbar/ic_new_clock.svg",
  total_cost: "/assets/icons/ic_dollar.svg",
  total_token_count: "/assets/icons/user/user_total_tokens_used.svg",
  input_tokens: "/assets/icons/user/user_total_tokens_used.svg",
  output_tokens: "/assets/icons/user/user_total_tokens_used.svg",
};

// Calculate visible percentage for each element
export const getVisiblePercentage = (elementRect, containerRect) => {
  if (!elementRect || !containerRect) return 0;

  const elementHeight = elementRect.height || 0;
  if (elementHeight === 0) return 0;

  const visibleTop = Math.max(elementRect.top, containerRect.top);
  const visibleBottom = Math.min(elementRect.bottom, containerRect.bottom);
  const visibleHeight = Math.max(0, visibleBottom - visibleTop);

  return (visibleHeight / elementHeight) * 100;
};

export const defaultFilter = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
};

export const filterDefinition = [
  {
    propertyName: "Session ID",
    propertyId: "session_id",
    filterType: {
      type: "text",
    },
  },
  {
    propertyName: "First Message",
    propertyId: "first_message",
    filterType: {
      type: "text",
    },
  },
  {
    propertyName: "Last Message",
    propertyId: "last_message",
    filterType: {
      type: "text",
    },
  },
  {
    propertyName: "Duration",
    propertyId: "duration",
    filterType: {
      type: "number",
    },
  },
  {
    propertyName: "Total Traces",
    propertyId: "total_traces_count",
    filterType: {
      type: "number",
    },
  },
  {
    propertyName: "Total Cost",
    propertyId: "total_cost",
    filterType: {
      type: "number",
    },
  },
  {
    propertyName: "Start Time",
    propertyId: "start_time",
    filterType: {
      type: "date",
    },
  },
  {
    propertyName: "End Time",
    propertyId: "end_time",
    filterType: {
      type: "date",
    },
  },
  {
    propertyName: "userId",
    propertyId: "user_id",
    filterType: {
      type: "text",
    },
  },
];

export const initialVisibility = {
  session_id: true,
  first_message: true,
  last_message: true,
  duration: true,
  total_cost: true,
  total_traces_count: true,
  start_time: true,
  end_time: true,
  user_id: true,
};
