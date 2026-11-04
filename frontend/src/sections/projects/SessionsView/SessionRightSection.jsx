import { Button, IconButton } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";

const SessionRightSection = React.forwardRef(
  ({ setOpenColumnConfigure }, columnConfigureRef) => {
    return (
      <Button
        size="small"
        sx={{
          marginRight: 1.5,
          width: 30,
          margin: 0,
          minWidth: 0,
          "& .MuiButton-startIcon": {
            margin: 0,
          },
        }}
        ref={columnConfigureRef}
        onClick={() => setOpenColumnConfigure(true)}
      >
        <IconButton>
          <SvgColor
            src="/assets/icons/action_buttons/ic_column.svg"
            sx={{ height: "18px", width: "18px", color: "text.primary" }}
          />
        </IconButton>
      </Button>
    );
  },
);

SessionRightSection.displayName = "SessionRightSection";

SessionRightSection.propTypes = {
  setOpenColumnConfigure: PropTypes.func,
};

export default SessionRightSection;
