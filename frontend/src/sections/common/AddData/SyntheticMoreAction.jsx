import React from "react";
import PropTypes from "prop-types";
import { Button } from "@mui/material";
import { isLaunchBuild, isSurfaceShown } from "src/launch/launchSwitch";
import { useGenerateRows } from "./GenerateRowsDialog";

export default function SyntheticMoreAction({
  datasetId,
  count,
  disabled,
  resolveRows,
}) {
  if (!isLaunchBuild() || !isSurfaceShown("synthetic.extras")) return null;
  return (
    <Button
      size="small"
      color="inherit"
      disabled={disabled || count > 50}
      onClick={() =>
        resolveRows((rows) => {
          if (rows.length && rows.length <= 50)
            useGenerateRows
              .getState()
              .open({ datasetId, rowIds: rows.map((row) => row.row_id) });
        })
      }
    >
      {`Generate more like these (${count})`}
    </Button>
  );
}
SyntheticMoreAction.propTypes = {
  datasetId: PropTypes.string.isRequired,
  count: PropTypes.number.isRequired,
  disabled: PropTypes.bool,
  resolveRows: PropTypes.func.isRequired,
};
