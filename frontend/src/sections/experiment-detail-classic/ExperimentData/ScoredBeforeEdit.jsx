import { Box, Chip, Tooltip } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CustomCellRender from "src/sections/common/DevelopCellRenderer/CustomCellRender";

// An evaluation edited in place keeps the reference column's old scores
// (the server marks them); they read as from before the edit, not current.
export const SCORED_BEFORE_EDIT_CHIP = "Scored before edit";
export const SCORED_BEFORE_EDIT_CHIP_TOOLTIP =
  "These scores are from this evaluation before it was edited.";
export const SCORED_BEFORE_EDIT_CELL_TOOLTIP =
  "Scored before the evaluation was edited";

export const isColumnScoredBeforeEdit = (col) =>
  Boolean(col?.scored_before_edit ?? col?.scoredBeforeEdit);

export const isCellScoredBeforeEdit = (cell) => {
  const infos = cell?.value_infos ?? cell?.valueInfos;
  return Boolean(infos?.scored_before_edit ?? infos?.scoredBeforeEdit);
};

export function ScoredBeforeEditChip({ col }) {
  if (!isColumnScoredBeforeEdit(col)) return null;
  return (
    <Tooltip title={SCORED_BEFORE_EDIT_CHIP_TOOLTIP} arrow>
      <Chip
        size="small"
        variant="outlined"
        label={SCORED_BEFORE_EDIT_CHIP}
        sx={{ flexShrink: 0, height: 20, typography: "s3" }}
      />
    </Tooltip>
  );
}

ScoredBeforeEditChip.propTypes = {
  col: PropTypes.object,
};

/** The grid cell: a score from before the edit keeps its value, muted. */
export function ScoredBeforeEditCell(props) {
  const cell = props?.data?.[props?.colDef?.field];
  if (!isCellScoredBeforeEdit(cell)) return <CustomCellRender {...props} />;
  return (
    <Tooltip title={SCORED_BEFORE_EDIT_CELL_TOOLTIP} arrow>
      <Box
        data-testid="scored-before-edit-cell"
        sx={{ height: "100%", opacity: 0.55 }}
      >
        <CustomCellRender {...props} />
      </Box>
    </Tooltip>
  );
}

ScoredBeforeEditCell.propTypes = {
  data: PropTypes.object,
  colDef: PropTypes.object,
};
