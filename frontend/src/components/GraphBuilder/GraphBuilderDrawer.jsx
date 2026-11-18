import Dialog from "@mui/material/Dialog";
import React from "react";
import Slide from "@mui/material/Slide";
import PropTypes from "prop-types";
import { Box, IconButton, Typography } from "@mui/material";
import Iconify from "../iconify";
import GraphBuilder from "./GraphBuilder";

const Transition = React.forwardRef(function Transition(props, ref) {
  return <Slide direction="up" ref={ref} {...props} />;
});

const GraphBuilderDrawer = ({
  open,
  onClose,
  value,
  onChange,
  saveLoading,
  agentType,
}) => {
  return (
    <Dialog
      fullScreen
      open={open}
      onClose={onClose}
      TransitionComponent={Transition}
      PaperProps={{
        sx: {
          borderRadius: "0px !important",
        },
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column", height: "100vh" }}>
        <Box
          sx={{
            padding: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Typography typography="m3" fontWeight="fontWeightMedium">
            Flow Builder
          </Typography>
          <IconButton
            onClick={onClose}
            sx={{
              position: "absolute",
              top: "12px",
              right: "12px",
              color: "text.primary",
            }}
          >
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
        <Box sx={{ width: "100%", flex: 1, display: "flex" }}>
          <GraphBuilder
            value={value}
            onChange={onChange}
            onClose={onClose}
            saveLoading={saveLoading}
            agentType={agentType}
          />
        </Box>
      </Box>
    </Dialog>
  );
};

GraphBuilderDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  value: PropTypes.any,
  onChange: PropTypes.func,
  saveLoading: PropTypes.bool,
  agentType: PropTypes.string,
};

export default GraphBuilderDrawer;
