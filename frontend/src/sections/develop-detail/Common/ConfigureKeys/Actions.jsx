import { IconButton, Stack } from "@mui/material";
import React from "react";
import SvgColor from "../../../../components/svg-color/svg-color";
import PropTypes from "prop-types";

export default function Actions({ onEditClick, onDeleteClick }) {
  return (
    <Stack direction={"row"} alignItems={"center"} gap={1}>
      <IconButton
        sx={{
          color: "text.primary",
          border: "1px solid",
          borderColor: "divider",
          padding: (theme) => theme.spacing(0.625),
        }}
        onClick={onEditClick}
      >
        <SvgColor
          // @ts-ignore
          src="/assets/icons/ic_edit.svg"
          sx={{
            height: "20px",
            width: "20px",
          }}
        />
      </IconButton>
      <IconButton
        sx={{
          color: "text.primary",
          border: "1px solid",
          borderColor: "divider",
          padding: (theme) => theme.spacing(0.625),
        }}
        onClick={onDeleteClick}
      >
        <SvgColor
          // @ts-ignore
          src="/assets/icons/ic_delete.svg"
          sx={{
            height: "20px",
            width: "20px",
          }}
        />
      </IconButton>
    </Stack>
  );
}

Actions.propTypes = {
  onEditClick: PropTypes.func,
  onDeleteClick: PropTypes.func,
  isDeletingApiKey: PropTypes.bool,
};
