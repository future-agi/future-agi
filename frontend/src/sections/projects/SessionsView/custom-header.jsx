import React, { useRef } from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { Box, IconButton, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";

const CustomHeader = (props) => {
  const { displayName, showColumnMenu } = props;
  const refButton = useRef(null);
  const scheduleIconItems = ["Start Time", "Latency", "End Time", "Duration"];
  const isScheduleIcon = scheduleIconItems.includes(displayName);
  const renderIcon = () => {
    if (
      props?.group ||
      props?.column?.colDef?.col?.groupBy === "Evaluation Metrics"
    ) {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="solar:check-circle-linear" color="status.success.fg" />
        </Box>
      );
    } else if (props?.displayName === "Node Type") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="solar:graph-linear" color="text.secondary" />
        </Box>
      );
    } else if (props?.displayName === "User Email") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="solar:letter-linear" color="text.primary" />
        </Box>
      );
    } else if (props?.displayName === "User Phone Number") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="solar:phone-linear" color="text.primary" />
        </Box>
      );
    } else if (isScheduleIcon) {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify
            icon="solar:clock-circle-linear"
            width="16px"
            height="16px"
            color="text.secondary"
          />
        </Box>
      );
    } else if (props?.displayName === "Total Tokens") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify
            icon="solar:dollar-minimalistic-linear"
            color="text.disabled"
          />
        </Box>
      );
    } else if (props?.displayName === "Total Cost") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <SvgColor
            // @ts-ignore
            src={`/assets/icons/components/ic_cost.svg`}
            sx={{ width: 16, height: 16 }}
          />
        </Box>
      );
    } else if (props?.displayName === "Total Traces") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify icon="solar:folder-tree-linear" color="text.disabled" />
        </Box>
      );
    }
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
        }}
      >
        <SvgColor
          src="/assets/icons/ic_col_header.svg"
          sx={{
            height: 20,
            width: 20,
          }}
        />
      </Box>
    );
  };

  const handleMenuClick = () => {
    if (showColumnMenu) {
      showColumnMenu(refButton.current);
    }
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
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        {renderIcon()}
        <Typography
          variant="s1"
          fontWeight={"fontWeightMedium"}
          color={"text.secondary"}
        >
          {displayName}
        </Typography>
      </Box>
      <IconButton
        size="small"
        ref={refButton}
        onClick={handleMenuClick}
        aria-label={`Show menu for ${displayName} column`}
        title={`Column menu for ${displayName}`}
      >
        <Iconify icon="solar:menu-dots-vertical-linear" />
      </IconButton>
    </Box>
  );
};

// Validate Props
CustomHeader.propTypes = {
  displayName: PropTypes.string.isRequired,
  column: PropTypes.object.isRequired,
  api: PropTypes.object.isRequired,
  menuIcon: PropTypes.string,
  dataType: PropTypes.string,
  group: PropTypes.bool,
  showColumnMenu: PropTypes.func.isRequired,
};

export default CustomHeader;
