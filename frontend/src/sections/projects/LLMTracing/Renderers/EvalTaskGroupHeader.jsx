import React from "react";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";
import EvalSourceGlyph from "./EvalSourceGlyph";

// ---------------------------------------------------------------------------
// Group-header band for an Eval Task in the trace grid. Renders the task name
// plus its source glyph ("T" / "S" / "Se") — the level is a property of the
// Task, so the glyph lives here (once per task) rather than on every cell.
// ---------------------------------------------------------------------------

const wrapperStyle = {
  display: "flex",
  flexDirection: "row",
  alignItems: "center",
  gap: "6px",
  width: "100%",
  height: "100%",
  paddingLeft: "12px",
  paddingRight: "12px",
};

const EvalTaskGroupHeader = ({ displayName, evalSourceLevel }) => {
  const theme = useTheme();
  return (
    <div style={wrapperStyle}>
      <span
        style={{
          fontSize: "13px",
          color: theme.palette.text.primary,
          fontWeight: 600,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {displayName}
      </span>
      {evalSourceLevel && <EvalSourceGlyph level={evalSourceLevel} />}
    </div>
  );
};

EvalTaskGroupHeader.propTypes = {
  displayName: PropTypes.string,
  evalSourceLevel: PropTypes.string,
};

export default EvalTaskGroupHeader;
