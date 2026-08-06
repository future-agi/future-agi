import React from "react";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { getGlyphMeta } from "../evalTaskGrouping";

const wrapperStyle = {
  display: "flex",
  alignItems: "center",
  gap: "6px",
  width: "100%",
  height: "100%",
  paddingLeft: "12px",
  paddingRight: "12px",
};

const EvalTaskGroupHeader = ({ displayName, rowType }) => {
  const theme = useTheme();
  const glyph = getGlyphMeta(rowType);

  return (
    <div style={wrapperStyle}>
      <span
        style={{
          fontSize: "13px",
          fontWeight: 500,
          color: theme.palette.text.primary,
          lineHeight: 1.4,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {displayName}
      </span>
      {glyph && (
        <CustomTooltip show title={glyph.label} arrow placement="top" size="small">
          <span
            style={{
              flexShrink: 0,
              fontSize: "10px",
              fontWeight: 700,
              lineHeight: 1,
              padding: "3px 5px",
              borderRadius: "4px",
              border: `1px solid ${theme.palette.divider}`,
              color: theme.palette.text.secondary,
              backgroundColor: theme.palette.background.neutral,
              cursor: "default",
            }}
          >
            {glyph.code}
          </span>
        </CustomTooltip>
      )}
    </div>
  );
};

EvalTaskGroupHeader.propTypes = {
  displayName: PropTypes.string,
  rowType: PropTypes.string,
};

export default EvalTaskGroupHeader;
