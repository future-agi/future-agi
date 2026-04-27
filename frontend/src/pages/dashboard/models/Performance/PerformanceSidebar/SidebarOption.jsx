import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const SidebarOption = ({ title, onAddClick, children }) => {
  return (
    <Box
      sx={{
        padding: 1,
        width: "270px",
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      <Box
        sx={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography color="text.disabled" fontWeight={700} fontSize="16px">
          {title}
        </Typography>
        <IconButton color="primary" onClick={() => onAddClick()}>
          <Iconify icon="mingcute:add-line" />
        </IconButton>
      </Box>
      <Box sx={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        {children}
      </Box>
    </Box>
  );
};

SidebarOption.propTypes = {
  title: PropTypes.string.isRequired,
  onAddClick: PropTypes.func.isRequired,
  children: PropTypes.node,
};

export default SidebarOption;
