import { Box, Button, IconButton, Typography } from "@mui/material";
import React from "react";
import Iconify from "../../../components/iconify";
import SvgColor from "../../../components/svg-color";
import PropTypes from "prop-types";

const FixMyAgentHeader = ({ onClose }) => {
  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flexStart",
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column" }}>
        <Typography variant="m3" fontWeight="fontWeightSemiBold">
          Fix My Agent
        </Typography>
        <Typography variant="s1">
          Here are the top issues and their fixes to agent quality and reduce
          failures.
        </Typography>
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Button
          variant="outlined"
          size="small"
          sx={{
            flexShrink: 0,
          }}
          onClick={() => {
            window.open(
              "https://docs.futureagi.com/docs/simulation/features/fix-my-agent",
              "_blank",
            );
          }}
        >
          <SvgColor
            src="/assets/icons/agent/docs.svg"
            sx={{ height: 16, width: 16, mr: 1 }}
          />
          <Typography typography="s2" fontWeight="fontWeightMedium">
            View Docs
          </Typography>
        </Button>
        <IconButton
          onClick={onClose}
          size="small"
          sx={{
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </Box>
    </Box>
  );
};

FixMyAgentHeader.propTypes = {
  onClose: PropTypes.func,
};

export default FixMyAgentHeader;
