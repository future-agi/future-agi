import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import QuickFilter from "src/components/ComplexFilter/QuickFilterComponents/QuickFilter";
import Iconify from "src/components/iconify";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import NumericCell from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { interpolateColorBasedOnScore } from "src/utils/utils";

const colorAllowed = ["Evaluation Metrics", "Annotation Metrics"];
const IgnoredQuickFilters = ["run_name"];

const ProjectCustomCellRenderer = (params) => {
  const column = params.colDef.col;
  const outputType = column?.outputType;
  const shouldReverse = column?.reverseOutput;
  const formattedValue = shouldReverse
    ? (100 - parseFloat(params?.value)).toFixed(2)
    : parseFloat(params?.value).toFixed(2);
  const backgroundColor =
    colorAllowed.includes(column?.groupBy) && outputType !== OutputTypes.NUMERIC
      ? interpolateColorBasedOnScore(formattedValue, 100, shouldReverse)
      : "";
  const isEval = column?.groupBy === "Evaluation Metrics";
  const isSystemMetric = column?.groupBy === "System Metrics";
  const isAnnotation = column?.groupBy === "Annotation Metrics";
  const applyQuickFilters = params.applyQuickFilters;

  const renderEval = (val) => {
    if (outputType === OutputTypes.NUMERIC) {
      return <NumericCell value={val} sx={{ padding: "0" }} />;
    }
    if (Array.isArray(val)) {
      return (
        <Box
          sx={{
            display: "flex",
            gap: (theme) => theme.spacing(1),
            flexWrap: "wrap",
            height: "100%",
            alignItems: "center",
          }}
        >
          {val.join(" · ")}
        </Box>
      );
    }
    return `${formattedValue}%`;
  };

  const renderSystemMetric = (val) => {
    if (column?.id === "avg_latency") {
      return `${val} ms`;
    }
    return `$ ${val}`;
  };

  const renderValue = () => {
    if (params.value === null || params.value === undefined) {
      return "-";
    }

    if (isSystemMetric) {
      return renderSystemMetric(params.value);
    }
    if (isEval) {
      return renderEval(params.value);
    }

    if (isAnnotation) {
      return renderEval(params.value);
    }

    return params.value;
  };

  if (params.value === null || params.value === undefined) {
    return (
      <Box
        sx={{
          paddingX: (theme) => theme.spacing(2),
          display: "flex",
          alignItems: "center",
          gap: (theme) => theme.spacing(1),
          height: "100%",
        }}
      >
        -
      </Box>
    );
  }

  return (
    <QuickFilter
      show={!IgnoredQuickFilters.includes(column.id)}
      onClick={(e) =>
        applyQuickFilters({
          col: column,
          value: params.value,
          filterAnchor: {
            top: e.clientY,
            left: e.clientX,
          },
        })
      }
    >
      <Box
        sx={{
          paddingX: (theme) => theme.spacing(2),
          backgroundColor,
          display: "flex",
          alignItems: "center",
          gap: (theme) => theme.spacing(1),
          height: "100%",
        }}
      >
        {renderValue()}
        {column.id === "rank" &&
        params?.data?.rank === 1 &&
        params?.node?.rowPinned !== "bottom" ? (
          <Iconify
            icon="solar:crown-bold"
            sx={{ color: "status.warning.solid" }}
          />
        ) : (
          <></>
        )}
      </Box>
    </QuickFilter>
  );
};

ProjectCustomCellRenderer.propTypes = {
  value: PropTypes.any,
};

export default ProjectCustomCellRenderer;
