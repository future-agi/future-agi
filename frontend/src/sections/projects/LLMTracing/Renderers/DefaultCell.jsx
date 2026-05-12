import React, { memo } from "react";
import { format } from "date-fns";
import QuickFilter from "src/components/ComplexFilter/QuickFilterComponents/QuickFilter";
import { RenderJSONString } from "src/components/custom-json-viewer/CustomJsonViewer";
import { RENDERER_CONFIG } from "./common";
import PropTypes from "prop-types";

// Memoize renderJson to avoid recreating on every render
const renderJson = (val) => {
  return <RenderJSONString val={val} />;
};

const DefaultCell = memo(
  ({
    value,
    column,
    backgroundColor,
    color,
    alignRight,
    applyQuickFilters,
    onCellClick,
  }) => {
    const colId = column?.id;

    const showQuickFilter =
      !RENDERER_CONFIG.ignoredQuickFilters.includes(colId) &&
      column?.groupBy !== "Custom Columns";

    const shouldApplyDateFormat =
      RENDERER_CONFIG.applyDateFormat.includes(colId) && value;

    const isClickable = colId === "trace_id" || colId === "span_id";

    let justifyContent;
    if (colId === "user_id") {
      justifyContent = "center";
    } else if (alignRight) {
      justifyContent = "flex-end";
    }

    let renderedValue;
    if (shouldApplyDateFormat) {
      try {
        renderedValue = format(new Date(value), "dd/MM/yyyy - HH:mm");
      } catch {
        renderedValue = value;
      }
    } else if (typeof value === "object" && value !== null) {
      renderedValue = renderJson(value);
    } else if (typeof value === "boolean") {
      renderedValue = String(value);
    } else {
      renderedValue = value;
    }

    const containerStyle = {
      backgroundColor,
      height: "100%",
      width: "100%",
      display: "flex",
      justifyContent,
    };

    const textStyle = {
      fontSize: "13px",
      fontWeight: 400,
      color: color || "text.primary",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
      cursor: isClickable ? "pointer" : "default",
    };

    const handleCellClick = (e) => {
      e.stopPropagation();
      onCellClick?.(e);
    };

    const handleQuickFilterClick = (e) => {
      e.stopPropagation();
      applyQuickFilters?.({
        col: column,
        value,
        filterAnchor: {
          top: e.clientY,
          left: e.clientX,
        },
      });
    };

    const wrapperStyle = { width: "100%", height: "100%" };

    const cellContent = (
      <div style={containerStyle} onClick={handleCellClick}>
        <span className="default-cell-text" style={textStyle}>
          {renderedValue}
        </span>
      </div>
    );

    if (showQuickFilter) {
      return (
        <div style={wrapperStyle}>
          <QuickFilter show={showQuickFilter} onClick={handleQuickFilterClick}>
            {cellContent}
          </QuickFilter>
        </div>
      );
    }

    return <div style={wrapperStyle}>{cellContent}</div>;
  },
);

DefaultCell.displayName = "DefaultCell";

DefaultCell.propTypes = {
  value: PropTypes.any,
  column: PropTypes.object,
  backgroundColor: PropTypes.string,
  color: PropTypes.string,
  alignRight: PropTypes.bool,
  applyQuickFilters: PropTypes.func,
  onCellClick: PropTypes.func,
};

export default DefaultCell;
