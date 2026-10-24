import React from "react";
import PropTypes from "prop-types";

import Button from "@mui/material/Button";

import EmptyContent from "src/components/empty-content";
import Iconify from "src/components/iconify";
import { statusColors } from "src/utils/runStatus";

// ----------------------------------------------------------------------
// Design System error state: the empty-state layout with the icon square in
// status.danger, a plain-language title, the detail in tertiary text and a
// Retry action when a retry function exists.

export default function ErrorState({
  title = "Something went wrong",
  detail,
  onRetry,
  sx,
  ...other
}) {
  return (
    <EmptyContent
      icon="solar:danger-triangle-linear"
      tone={(theme) => statusColors(theme, "danger")}
      title={title}
      description={detail}
      action={
        onRetry ? (
          <Button
            variant="outlined"
            color="inherit"
            size="small"
            startIcon={<Iconify icon="solar:refresh-linear" width={16} />}
            onClick={onRetry}
          >
            Retry
          </Button>
        ) : undefined
      }
      sx={sx}
      {...other}
    />
  );
}

ErrorState.propTypes = {
  title: PropTypes.node,
  detail: PropTypes.node,
  onRetry: PropTypes.func,
  sx: PropTypes.object,
};
