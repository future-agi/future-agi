import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";
import HeaderIcon from "./HeaderIcon";
import EvalTargetGlyph from "src/components/traceDetail/EvalRollupSection/EvalTargetGlyph";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";

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
  enableSorting = false,
  progressSort,
}) => {
  const theme = useTheme();
  const [sort, setSort] = useState(column?.getSort?.() || null);
  useEffect(() => {
    const changed = () => setSort(column?.getSort?.() || null);
    column?.addEventListener?.("sortChanged", changed);
    return () => column?.removeEventListener?.("sortChanged", changed);
  }, [column]);
  const Wrapper = enableSorting ? "button" : "div";

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
  const targetType =
    column?.colDef?.context?.sourceColumn?.targetType ||
    column?.colDef?.context?.sourceColumn?.target_type;

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
    <Wrapper
      style={{
        ...wrapperStyle,
        ...(enableSorting
          ? {
              border: 0,
              background: "transparent",
              cursor: "pointer",
              font: "inherit",
            }
          : {}),
      }}
      {...(enableSorting
        ? {
            type: "button",
            "aria-label": `Sort ${displayName}`,
            title: sort
              ? `Sorted ${sort === "asc" ? "ascending" : "descending"}; click to change`
              : "Sort by this column",
            onClick: (event) => progressSort?.(event.shiftKey),
          }
        : {})}
    >
      <HeaderIcon
        displayName={displayName}
        isGroup={isGroupHeader}
        isEvaluationMetric={isEvaluationMetric}
      />
      {isEvaluationMetric && <EvalTargetGlyph targetType={targetType} />}
      <OverflowTooltip title={displayName}>
        <span style={textStyle}>{displayName}</span>
      </OverflowTooltip>
      {enableSorting && (
        <svg
          aria-hidden="true"
          width="12"
          height="14"
          viewBox="0 0 12 14"
          style={{ flexShrink: 0, color: theme.palette.text.secondary }}
        >
          <path
            d={
              sort === "desc" ? "M6 2v10m-4-4 4 4 4-4" : "M6 12V2m-4 4 4-4 4 4"
            }
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            opacity={sort ? 1 : 0.35}
          />
        </svg>
      )}
    </Wrapper>
  );
};

CustomTraceHeaderRenderer.propTypes = {
  displayName: PropTypes.string,
  group: PropTypes.oneOfType([PropTypes.bool, PropTypes.string]),
  column: PropTypes.object,
  eGridHeader: PropTypes.object,
  enableSorting: PropTypes.bool,
  progressSort: PropTypes.func,
};

export default CustomTraceHeaderRenderer;
