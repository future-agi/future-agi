import { Button } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const RunPromptButton = ({ children, ...props }) => {
  return (
    <Button
      variant="contained"
      color="primary"
      size="small"
      sx={{ borderRadius: "4px", minWidth: "130px", whiteSpace: "nowrap" }}
      startIcon={
        <Iconify
          icon="lucide:play"
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
      {children}
    </Button>
  );
};

RunPromptButton.propTypes = {
  children: PropTypes.any,
};

export default RunPromptButton;
