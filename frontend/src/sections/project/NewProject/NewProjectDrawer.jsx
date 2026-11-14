import { Box, Drawer, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useEffect } from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import { useLocation } from "react-router-dom";

import NewExperiment from "./NewExperiment";
import NewObserve from "./NewObserve";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const NewProjectDrawer = ({ open, onClose }) => {
  const location = useLocation();
  const currentPath = location.pathname;
  const [isObserve, setIsObserve] = useState(currentPath.includes("observe"));
  useEffect(() => {
    const isObserve = currentPath.includes("observe");
    setIsObserve(isObserve);
  }, [currentPath]);

  const covered = useDrawerCovered(open);

  return (
    <Drawer
      sx={coveredDrawerSx(covered)}
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <Box sx={{ width: "80vw", padding: 2 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography
            veriant="m3"
            fontWeight={"fontWeightMedium"}
            color="text.primary"
          >
            New Projects
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
        {/* <Box sx={{ marginTop: "24px" }} /> */}
        {/* <ToggleButtonGroup sx={{ padding: 0.5, gap: 0.5 }}>
          <Button
            size="small"
            variant={selected === "experiment" ? "soft" : "text"}
            color={selected === "experiment" ? "primary" : "inherit"}
            onClick={() => setSelected("experiment")}
          >
            Experiment
          </Button>
          <Button
            size="small"
            variant={selected === "observe" ? "soft" : "text"}
            color={selected === "observe" ? "primary" : "inherit"}
            onClick={() => setSelected("observe")}
          >
            Observe
          </Button>
        </ToggleButtonGroup> */}
        <Box sx={{ marginTop: "12px" }} />
        <ShowComponent condition={!isObserve}>
          <NewExperiment />
        </ShowComponent>
        <ShowComponent condition={isObserve}>
          <NewObserve />
        </ShowComponent>
      </Box>
    </Drawer>
  );
};

NewProjectDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  isObserve: PropTypes.bool,
  isPrototype: PropTypes.bool,
};

export default NewProjectDrawer;
