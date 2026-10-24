import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const DrawerHeaderbar = (props) => {
  const { onClose, title, actionButton, showCloseIcon = true } = props;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 16px 15px 16px",
      }}
    >
      <Typography
        variant="body2"
        fontWeight="fontWeightBold"
        color="text.primary"
      >
        {title}
      </Typography>
      <Box sx={{ display: "flex", alignItems: "center", gap: "20px" }}>
        {actionButton}
        {showCloseIcon ? (
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        ) : null}
      </Box>
    </Box>
  );
};

DrawerHeaderbar.propTypes = {
  title: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
  actionButton: PropTypes.node,
  showCloseIcon: PropTypes.bool,
};

export default DrawerHeaderbar;
