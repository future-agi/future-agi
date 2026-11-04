import { IconButton } from "@mui/material";
import React from "react";
import SvgColor from "../../svg-color";
import PropTypes from "prop-types";

const DeleteAction = ({
  handleDeleteRow,
  node,
  api,
  isGenerating,
  disabled,
}) => {
  const isDisabled =
    (node.rowIndex === 0 && api.getDisplayedRowCount() === 1) ||
    isGenerating ||
    disabled;

  return (
    <IconButton
      size="small"
      onClick={() => {
        if (isDisabled) return;
        handleDeleteRow(node.rowIndex);
      }}
    >
      <SvgColor
        src="/assets/icons/ic_delete.svg"
        sx={{
          width: "20px",
          height: "20px",
          color: isDisabled ? "text.disabled" : "text.primary",
        }}
      />
    </IconButton>
  );
};

DeleteAction.propTypes = {
  handleDeleteRow: PropTypes.func,
  node: PropTypes.object,
  api: PropTypes.any,
  isGenerating: PropTypes.bool,
  disabled: PropTypes.bool,
};

export default DeleteAction;
