import React from "react";
import PropTypes from "prop-types";
import { IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useColumnFromPathStore } from "src/sections/common/Values/columnFromPathStore";

/**
 * IT-R2 G5 P5: the `+` at the end of the Data grid header opens `Add column
 * from path` (sample values + coverage per path). A narrow pinned-right
 * column; the column catalog (View › Columns) does not list it.
 */
export const ADD_COLUMN_COL_ID = "__add_column__";

const AddColumnHeader = ({ datasetId }) => (
  <CustomTooltip
    show
    title="Add a column from a path (e.g. metadata.customer.tier)"
    placement="bottom"
    size="small"
  >
    <IconButton
      size="small"
      aria-label="Add column from path"
      data-testid="grid-add-column"
      onClick={() => useColumnFromPathStore.getState().openFor({ datasetId })}
      sx={{
        width: 28,
        height: 28,
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: 1,
        color: "text.secondary",
        "&:hover": { color: "primary.main", borderColor: "primary.main" },
      }}
    >
      <Iconify icon="mdi:plus" width={16} />
    </IconButton>
  </CustomTooltip>
);

AddColumnHeader.propTypes = { datasetId: PropTypes.string };

/** The pinned-right `+` column for a dataset's grid. */
export const addColumnDef = (datasetId) => ({
  colId: ADD_COLUMN_COL_ID,
  headerName: "",
  width: 48,
  minWidth: 48,
  maxWidth: 48,
  pinned: "right",
  lockPinned: true,
  lockPosition: "right",
  resizable: false,
  sortable: false,
  suppressMovable: true,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
  suppressNavigable: true,
  suppressColumnsToolPanel: true,
  headerComponent: AddColumnHeader,
  headerComponentParams: { datasetId },
  cellRenderer: () => null,
  cellStyle: { padding: 0 },
});

export default AddColumnHeader;
