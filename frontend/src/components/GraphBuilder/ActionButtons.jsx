import { IconButton } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";

const ActionButtons = ({ icon, sx, ...rest }) => {
  return (
    <IconButton
      {...rest}
      disableTouchRipple
      disableFocusRipple
      sx={[
        {
          border: "1px solid",
          borderColor: "border.default",
          color: "text.secondary",
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      {icon}
    </IconButton>
  );
};

ActionButtons.propTypes = {
  icon: PropTypes.node,
  sx: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
};

export default ActionButtons;
