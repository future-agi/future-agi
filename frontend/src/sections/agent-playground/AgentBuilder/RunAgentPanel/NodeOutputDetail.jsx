import {
  Box,
  Divider,
  Typography,
  Switch,
  FormControlLabel,
  CircularProgress,
  Skeleton,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState, useCallback, useRef } from "react";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { useGetNodeExecutionDetail } from "src/api/agent-playground/agent-playground";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";

const tryParseJson = (str) => {
  if (typeof str !== "string") return null;
  const trimmed = str.trim();
  if (
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]"))
  ) {
    try {
      const parsed = JSON.parse(trimmed);
      if (typeof parsed === "object" && parsed !== null) return parsed;
    } catch {
      // not valid JSON
    }
  }
  return null;
};

const PayloadContent = ({ value }) => {
  if (!value) {
    return (
      <Box
        sx={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          px: 1,
          overflowY: "auto",
        }}
      >
        <Typography typography="s2" color="text.secondary">
          NA
        </Typography>
      </Box>
    );
  }

  if (typeof value === "object" && value !== null) {
    return (
      <Box
        sx={{
          height: "100%",
          padding: "4px 8px",
          overflowY: "auto",
          wordBreak: "break-word",
          "& *": { whiteSpace: "pre-wrap !important" },
        }}
      >
        <CustomJsonViewer
          object={value}
          defaultInspectDepth={2}
          collapseStringsAfterLength={false}
        />
      </Box>
    );
  }

  const parsedJson = tryParseJson(value);
  if (parsedJson) {
    return (
      <Box
        sx={{
          height: "100%",
          padding: "4px 8px",
          overflowY: "auto",
          wordBreak: "break-word",
          "& *": { whiteSpace: "pre-wrap !important" },
        }}
      >
        <CustomJsonViewer
          object={parsedJson}
          defaultInspectDepth={2}
          collapseStringsAfterLength={false}
        />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        height: "100%",
        padding: "4px 8px",
        whiteSpace: "pre-wrap",
        overflowY: "auto",
        textOverflow: "ellipsis",
        typography: "s2",
      }}
    >
      {value}
    </Box>
  );
};

PayloadContent.propTypes = {
  value: PropTypes.any,
};

// Used in paired mode (equal input/output counts) — renders a single payload string
const TextCellRendererWrapper = ({ value }) => <PayloadContent value={value} />;

TextCellRendererWrapper.propTypes = {
  value: PropTypes.string,
};

// Used in stacked mode (unequal counts) — renders array of ports vertically
const PortListCellRenderer = ({ value }) => {
  const ports = value || [];

  if (ports.length === 0) {
    return (
      <Box
        sx={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          px: 1,
        }}
      >
        <Typography typography="s2" color="text.secondary">
          NA
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflowY: "auto",
      }}
    >
      {ports.map((port, i) => (
        <Box key={port.portId || i}>
          {i > 0 && <Divider />}
          <Box sx={{ p: "4px 8px" }}>
            <PayloadContent value={port.payload ?? ""} />
          </Box>
        </Box>
      ))}
    </Box>
  );
};

PortListCellRenderer.propTypes = {
  value: PropTypes.array,
};

const SkeletonCellRenderer = () => (
  <Box
    sx={{
      width: "100%",
      height: "100%",
      p: 1,
      display: "flex",
      flexDirection: "column",
      gap: 1,
    }}
  >
    <Skeleton variant="rectangular" animation="wave" height={10} />
    <Skeleton variant="rectangular" animation="wave" height={10} width="80%" />
    <Skeleton variant="rectangular" animation="wave" height={10} width="60%" />
  </Box>
);

const ErrorCellRenderer = ({ data }) => (
  <Box
    sx={{
      height: "100%",
      overflowY: "auto",
      padding: "4px 8px",
      wordBreak: "break-word",
    }}
  >
    <Typography
      typography="s2"
      color="error.main"
      sx={{ whiteSpace: "pre-wrap" }}
    >
      {data.output}
    </Typography>
  </Box>
);

ErrorCellRenderer.propTypes = {
  data: PropTypes.object,
};

const NODE_OUTPUT_THEME_PARAMS = {
  columnBorder: false,
  headerColumnBorder: { width: 0 },
};

export default function NodeOutputDetail({ executionId, nodeExecutionId }) {
  const agTheme = useAgThemeWith(NODE_OUTPUT_THEME_PARAMS);
  const gridApiRef = useRef(null);
  const [showInputs, setShowInputs] = useState(false);

  const {
    data: nodeDetail,
    isLoading,
    isError,
  } = useGetNodeExecutionDetail(executionId, nodeExecutionId);

  const handleToggleInputs = useCallback((event) => {
    setShowInputs(event.target.checked);
  }, []);

  const nodeStatus = nodeDetail?.status?.toLowerCase();
  const isNodeRunning = nodeStatus === "running" || nodeStatus === "pending";

  const inputs = nodeDetail?.inputs || [];
  const outputs = nodeDetail?.outputs || [];
  const hasErrorMessage = !!nodeDetail?.errorMessage && outputs.length === 0;
  const isPairedMode = inputs.length > 0 && inputs.length === outputs.length;

  // Map API response to AG Grid row data
  const rowData = useMemo(() => {
    if (!nodeDetail) return [];

    if (isNodeRunning) {
      if (inputs.length > 0) {
        return inputs.map((inp, i) => ({
          id: `${nodeDetail.nodeExecutionId}-${i}`,
          input: inp?.payload ?? "",
          _running: true,
        }));
      }
      return [{ id: nodeDetail.nodeExecutionId, _running: true }];
    }

    if (hasErrorMessage) {
      if (inputs.length > 0) {
        // Has inputs but output errored — one row per input
        return inputs.map((inp, i) => ({
          id: `${nodeDetail.nodeExecutionId}-${i}`,
          input: inp?.payload ?? "",
          output: nodeDetail.errorMessage,
        }));
      }
      // No inputs and no outputs
      return [
        {
          id: nodeDetail.nodeExecutionId,
          inputs: [],
          outputs: [],
          output: nodeDetail.errorMessage,
        },
      ];
    }

    if (isPairedMode) {
      // Equal counts — one row per input/output pair
      return inputs.map((inp, i) => ({
        id: `${nodeDetail.nodeExecutionId}-${i}`,
        input: inp?.payload ?? "",
        output: outputs[i]?.payload ?? "",
      }));
    }

    // Unequal counts — single row, each cell gets full port array
    return [
      {
        id: nodeDetail.nodeExecutionId,
        inputs,
        outputs,
      },
    ];
  }, [
    nodeDetail,
    hasErrorMessage,
    isPairedMode,
    isNodeRunning,
    inputs,
    outputs,
  ]);

  // Column definitions
  const columnDefs = useMemo(() => {
    if (isNodeRunning) {
      const cols = [];
      if (showInputs) {
        cols.push({
          field: "input",
          headerName: "Input",
          minWidth: 300,
          flex: 1,
          cellRendererSelector: (params) => {
            if (params.value) return { component: TextCellRendererWrapper };
            return { component: SkeletonCellRenderer };
          },
        });
      }
      cols.push({
        field: "output",
        headerName: "Output",
        minWidth: showInputs ? 400 : undefined,
        flex: showInputs ? 2 : 1,
        cellRenderer: SkeletonCellRenderer,
      });
      return cols;
    }

    const usePairedInput =
      isPairedMode || (hasErrorMessage && inputs.length > 0);
    const cols = [];

    if (showInputs) {
      cols.push(
        usePairedInput
          ? {
              field: "input",
              headerName: "Input",
              minWidth: 300,
              flex: 1,
              cellRenderer: TextCellRendererWrapper,
            }
          : {
              field: "inputs",
              headerName: "Input",
              minWidth: 300,
              flex: 1,
              cellRenderer: PortListCellRenderer,
            },
      );
    }

    if (hasErrorMessage) {
      cols.push({
        field: "output",
        headerName: "Output",
        minWidth: 400,
        flex: 2,
        cellRenderer: ErrorCellRenderer,
      });
    } else {
      cols.push(
        isPairedMode
          ? {
              field: "output",
              headerName: "Output",
              minWidth: 400,
              flex: 2,
              cellRenderer: TextCellRendererWrapper,
            }
          : {
              field: "outputs",
              headerName: "Output",
              minWidth: 400,
              flex: 2,
              cellRenderer: PortListCellRenderer,
            },
      );
    }

    return cols;
  }, [showInputs, hasErrorMessage, isPairedMode, isNodeRunning, inputs.length]);

  const PORT_ROW_HEIGHT = 120;

  const getRowHeight = useCallback(
    (params) => {
      if (isPairedMode) return 500;
      const maxPorts = Math.max(
        params.data.inputs?.length || 0,
        params.data.outputs?.length || 0,
      );
      return Math.max(500, maxPorts * PORT_ROW_HEIGHT);
    },
    [isPairedMode],
  );

  const defaultColDef = useMemo(
    () => ({
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      filter: false,
      resizable: true,
      lockVisible: true,
      cellStyle: {
        padding: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        height: "100%",
      },
    }),
    [],
  );

  if (!nodeExecutionId) {
    return (
      <Box
        sx={{
          flex: 1,
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          bgcolor: "background.paper",
        }}
      >
        <Typography typography="s2" color="text.disabled">
          Select a node to view details
        </Typography>
      </Box>
    );
  }

  if (isLoading) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: 1,
          height: "100%",
        }}
      >
        <CircularProgress size={24} />
        <Typography typography="s2" color="text.secondary">
          Loading node details...
        </Typography>
      </Box>
    );
  }

  if (isError) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography typography="s2" color="error.main">
          Failed to load node details
        </Typography>
      </Box>
    );
  }

  if (!nodeDetail) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography typography="s2" color="text.disabled">
          No data available for this node
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        p: 2,
        overflowY: "auto",
        backgroundColor: "background.paper",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 2,
        }}
      >
        <Box>
          <Typography
            typography="m3"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            Agent flow results
          </Typography>
        </Box>

        <FormControlLabel
          control={
            <Switch
              checked={showInputs}
              onChange={handleToggleInputs}
              size="small"
            />
          }
          label={
            <Typography typography="s2" color="text.secondary">
              Show inputs
            </Typography>
          }
        />
      </Box>

      {/* AG Grid Table */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <AgGridReact
          ref={gridApiRef}
          rowData={rowData}
          getRowHeight={getRowHeight}
          theme={agTheme}
          domLayout="autoHeight"
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          getRowId={(params) => params.data.id}
        />
      </Box>
    </Box>
  );
}

NodeOutputDetail.propTypes = {
  executionId: PropTypes.string,
  nodeExecutionId: PropTypes.string,
};
