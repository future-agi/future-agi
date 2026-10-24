import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import Iconify from "src/components/iconify";
import CustomTooltip from "../../../components/tooltip";

const ColumnCellRenderer = (params) => {
  const { displayName, showColumnMenu, hideMenu } = params;
  const refButton = useRef(null);

  const onMenuClicked = () => {
    showColumnMenu(refButton?.current);
  };
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
      }}
    >
      <CustomTooltip show size="small" arrow title={displayName}>
        <Typography
          fontWeight={500}
          fontSize="13px"
          color={"text.secondary"}
          sx={{
            textOverflow: "ellipsis",
            overflow: "hidden",
            maxWidth: "95%",
          }}
        >
          {displayName}
        </Typography>
      </CustomTooltip>
      {!hideMenu && (
        <IconButton size="small" ref={refButton} onClick={onMenuClicked}>
          <Iconify icon="solar:menu-dots-vertical-linear" />
        </IconButton>
      )}
    </Box>
  );
};

ColumnCellRenderer.propTypes = {
  params: PropTypes.object,
};

export default ColumnCellRenderer;
