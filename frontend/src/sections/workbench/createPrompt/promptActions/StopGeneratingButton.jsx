import { Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { LoadingButton } from "@mui/lab";
import CustomTooltip from "src/components/tooltip";

const StopGeneratingButton = ({ children, sx, ...props }) => {
  return (
    <CustomTooltip size="small" title="Stop Generating" arrow show={true}>
      <LoadingButton
        variant="outlined"
        size="small"
        sx={{
          width: "133px",
          ...sx,
        }}
        startIcon={
          <Iconify
            icon="solar:stop-circle-linear"
            color="text.primary"
            width="16px"
            height="16px"
            sx={{
              cursor: "pointer",
              marginRight: "-4px",
            }}
          />
        }
        {...props}
      >
        <Typography typography="s2" fontWeight={"fontWeightSemiBold"}>
          {children}
        </Typography>
      </LoadingButton>
    </CustomTooltip>
  );
};

StopGeneratingButton.propTypes = {
  children: PropTypes.node,
  sx: PropTypes.object,
};

export default StopGeneratingButton;
