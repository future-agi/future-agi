import React from "react";
import PropTypes from "prop-types";
import EvaluateCell from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/EvaluateCell";
import { Box } from "@mui/material";
const EvaluateCellRenderer = (props) => {
  const { value, data, colDef } = props;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        width: "100%",
        justifyContent: "center",
      }}
    >
      <EvaluateCell
        value={value}
        dataType={colDef.cellRendererParams?.dataType}
        meta={data?.meta}
        isFutureAgiEval={colDef.isFutureAgiEval}
        cellData={{ ...data, cellValue: value }}
        originType={colDef.originType}
        choicesMap={colDef.choicesMap}
      />
    </Box>
  );
};

EvaluateCellRenderer.propTypes = {
  value: PropTypes.any,
  data: PropTypes.object,
  colDef: PropTypes.object,
};

export default EvaluateCellRenderer;
