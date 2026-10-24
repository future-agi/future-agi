import { Box, Skeleton, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import {
  getLabel,
  evalResultRole,
} from "src/sections/develop-detail/DataTab/common";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { statusColors } from "src/utils/runStatus";
import { commonBorder } from "src/sections/experiment-detail/ExperimentData/Common";
import { getUniqueColorPalette } from "src/utils/utils";
import CompareEvalGrid from "../CompareDrawer/CompareEvalGrid";

const SkeletonLoader = () => (
  <Box
    sx={{
      paddingX: 1,
      display: "flex",
      alignItems: "center",
      height: "100%",
    }}
  >
    <Skeleton sx={{ width: "100%", height: "10px" }} variant="rounded" />
  </Box>
);

const StatusCellRenderer = (data) => {
  const theme = useTheme();

  let cellValue = data?.data?.data?.cellValue;
  const status = data?.data?.status;

  if (status === "running") {
    return <SkeletonLoader />;
  }
  if (status === "error") {
    return (
      <Box
        sx={{
          marginLeft: "10px",
          color: statusColors(theme, "danger").fg,
          fontSize: "13px",
        }}
      >
        Error
      </Box>
    );
  }

  if (cellValue?.startsWith("['") && cellValue.endsWith("']")) {
    cellValue = JSON.parse(cellValue.replace(/'/g, '"'));
  }
  if (
    !cellValue ||
    cellValue === "[]" ||
    cellValue === undefined ||
    cellValue === ""
  ) {
    return;
  }

  return (
    <Box>
      <StatusBadge
        label={getLabel(cellValue)}
        role={evalResultRole(data?.data?.data?.cellValue)}
        dot={false}
        sx={{ marginRight: "10px" }}
      />

      {Array.isArray(cellValue) && cellValue.length > 1 && (
        <StatusBadge
          label={`+${cellValue.length - 1}`}
          role={evalResultRole(data?.data?.data?.cellValue)}
          dot={false}
        />
      )}
    </Box>
  );
};

const ViewDetailsCellRenderer = (props) => {
  const { node, setEvalDrawer, setRunEval, disabled } = props;
  const theme = useTheme();

  const handleClick = () => {
    if (!disabled) {
      setEvalDrawer(true);
      setRunEval({
        ...node?.data?.data?.description,
        evalName: node?.data?.data?.evalName,
      });
    }
  };

  return (
    <a
      style={{
        color: disabled
          ? theme.palette.text.disabled
          : theme.palette.primary.main,
        cursor: disabled ? "not-allowed" : "pointer",
        pointerEvents: disabled ? "none" : "auto",
      }}
      onClick={handleClick}
    >
      View Details
    </a>
  );
};

const RunEvaluations = ({ traceData, selectedEvals, index }) => {
  const safeIndex = index % 10;
  const { tagBackground: bg, tagForeground: text } =
    getUniqueColorPalette(safeIndex);
  const theme = useTheme();

  const evaluationMetrics = useMemo(() => {
    return traceData?.evalsMetrics || {};
  }, [traceData?.evalsMetrics]);

  const rowData = useMemo(() => {
    const rows = Object.entries(evaluationMetrics).filter(([id]) =>
      selectedEvals.includes(id),
    );
    return rows.map((i) => i[1]);
  }, [evaluationMetrics, selectedEvals]);

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <Box
        sx={{
          display: "flex",
          gap: theme.spacing(1),
          alignItems: "center",
          borderBottom: commonBorder.border,
          borderRight: commonBorder.border,
          borderColor: commonBorder.borderColor,
          paddingY: theme.spacing(2.1),
          paddingX: theme.spacing(2),
          flexShrink: 0,
        }}
      >
        <Box
          sx={{
            width: theme.spacing(3),
            height: theme.spacing(3.125),
            borderRadius: theme.spacing(0.5),
            backgroundColor: bg,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 12,
            fontWeight: 600,
            color: text,
          }}
        >
          {String.fromCharCode(65 + index)}
        </Box>
        <Typography fontWeight={600}>
          {traceData?.projectVersionName}
        </Typography>
      </Box>
      <Box
        sx={{
          paddingY: theme.spacing(1),
          paddingX: theme.spacing(2),
          width: "100%",
          minHeight: 0,
          flex: 1,
          borderRight: commonBorder.border,
          borderColor: commonBorder.borderColor,
        }}
      >
        <CompareEvalGrid rowData={rowData} />
      </Box>
    </Box>
  );
};

RunEvaluations.propTypes = {
  traceData: PropTypes.object,
  selectedEvals: PropTypes.array,
  index: PropTypes.number,
};

StatusCellRenderer.propTypes = {
  value: PropTypes.string,
};

ViewDetailsCellRenderer.propTypes = {
  setEvalDrawer: PropTypes.func,
  setRunEval: PropTypes.func,
  node: PropTypes.shape({
    data: PropTypes.object,
  }),
  colDef: PropTypes.object,
  disabled: PropTypes.bool,
};

export default RunEvaluations;
