import React from "react";
import { IconButton } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import { useAuthContext } from "src/auth/hooks";
import { ROLES } from "src/utils/rolePermissionMapping";
import { isUnsavedRow } from "../common";

// Delete control shown only for unsaved, locally-added rows (never backend rows).
const DeleteRowCellRenderer = ({ data, api, onDelete }) => {
  const { role } = useAuthContext();
  const isViewer = role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;

  // Keep at least one row so the grid (which reads rows[0] / rows.at(-1)) stays valid.
  if (
    isViewer ||
    !isUnsavedRow(data) ||
    (api?.getDisplayedRowCount?.() ?? 1) <= 1
  ) {
    return null;
  }

  return (
      <IconButton
        size="small"
        aria-label="Delete empty row"
        onClick={() => onDelete(data.id)}
      >
        <SvgColor
          src="/assets/icons/ic_delete.svg"
          sx={{ width: 16, height: 16, color: "error.main" }}
        />
      </IconButton>
  );
};

DeleteRowCellRenderer.propTypes = {
  data: PropTypes.object,
  api: PropTypes.object,
  onDelete: PropTypes.func,
};

export default DeleteRowCellRenderer;
