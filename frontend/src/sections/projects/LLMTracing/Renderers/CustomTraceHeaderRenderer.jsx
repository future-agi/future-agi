import React, { useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";
import HeaderIcon from "./HeaderIcon";

const wrapperStyle = {
  display: "flex",
  flexDirection: "row",
  alignItems: "center",
  gap: "6px",
  position: "relative",
  width: "100%",
  height: "100%",
  paddingLeft: "12px",
  paddingRight: "12px",
};

const CustomTraceHeaderRenderer = ({
  displayName,
  group,
  column,
  eGridHeader,
}) => {
  const theme = useTheme();

  useEffect(() => {
    if (eGridHeader?.style) {
      eGridHeader.style.padding = 0;
    }
  }, [eGridHeader]);

  const isEvaluationMetric = useMemo(
    () =>
      column?.colDef?.context?.sourceColumn?.groupBy === "Evaluation Metrics",
    [column],
  );
  const isGroupHeader = Boolean(group);

  const textStyle = {
    fontSize: "13px",
    color: theme.palette.text.primary,
    fontWeight: 500,
    lineHeight: 1.4,
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  };

  return (
    <div style={wrapperStyle}>
      <HeaderIcon
        displayName={displayName}
        isGroup={isGroupHeader}
        isEvaluationMetric={isEvaluationMetric}
      />
      <span style={textStyle}>{displayName}</span>
    </div>
  );
};

CustomTraceHeaderRenderer.propTypes = {
  displayName: PropTypes.string,
  group: PropTypes.oneOfType([PropTypes.bool, PropTypes.string]),
  column: PropTypes.object,
  eGridHeader: PropTypes.object,
};

export default CustomTraceHeaderRenderer;
