import {
  Box,
  Breadcrumbs,
  Button,
  IconButton,
  Typography,
} from "@mui/material";
import React from "react";
import SvgColor from "../../../../components/svg-color/svg-color";
import PropTypes from "prop-types";
import { AGENT_TYPES } from "src/sections/agents/constants";

export default function Header({
  onClose,
  setCompareReplay,
  simulationCallType,
}) {
  const handleClose = () => {
    onClose();
    setCompareReplay(false);
  };

  const breadcrumbs = [
    <Button
      size="small"
      variant="text"
      key="1"
      onClick={() => {
        setCompareReplay(false);
      }}
      sx={{ color: "text.disabled" }}
    >
      {simulationCallType === AGENT_TYPES.VOICE
        ? "Call log details"
        : "Chat log details"}
    </Button>,
    <Typography
      typography="s1"
      fontWeight={"fontWeightMedium"}
      color="text.primary"
      key="2"
      sx={{
        px: "10px",
        "&:hover": {
          backgroundColor: "transparent",
        },
      }}
      onClick={() => {}}
    >
      Compare baseline vs replay
    </Typography>,
  ];
  return (
    <Box
      sx={{
        p: 2,
        py: 1,
        pl: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        position: "sticky",
        top: 0,
        zIndex: 1,
        backgroundColor: "background.paper",
      }}
    >
      <Breadcrumbs
        sx={{
          "& .MuiBreadcrumbs-separator": {
            marginLeft: 0,
            marginRight: 0,
          },
        }}
        separator={
          <SvgColor
            src="/assets/icons/custom/lucide--chevron-right.svg"
            sx={{ width: 20, height: 20, bgcolor: "text.primary" }}
          />
        }
        aria-label="breadcrumb"
      >
        {breadcrumbs}
      </Breadcrumbs>
      <IconButton
        onClick={handleClose}
        sx={{
          color: "text.primary",
        }}
        size="small"
      >
        <SvgColor
          sx={{
            height: "24px",
            width: "24px",
          }}
          src="/assets/icons/ic_close.svg"
        />
      </IconButton>
    </Box>
  );
}

Header.propTypes = {
  onClose: PropTypes.func.isRequired,
  setCompareReplay: PropTypes.func.isRequired,
  simulationCallType: PropTypes.string,
};
