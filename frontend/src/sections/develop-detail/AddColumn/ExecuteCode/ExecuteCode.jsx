import { Box, Button, Drawer, IconButton, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { useExecuteCodeStore } from "../../states";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { CODE_COLUMNS_DISABLED_REASON } from "./codeDisabled";

// the capability contract: custom Python code is turned off. However this panel is opened
// (the column menu, the Add column picker, a Conditional Node branch, or the
// store set directly), it shows the reason and sends nothing: no config read,
// no Test preview, no create and no Update / re-run. The column's existing
// values stay readable in the grid.
export const ExecuteCodeChild = ({ onClose }) => (
  <Box
    data-testid="execute-code-disabled"
    sx={{
      padding: "20px",
      display: "flex",
      flexDirection: "column",
      gap: 2,
      width: "550px",
    }}
  >
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}
    >
      <Typography fontWeight={600} color="text.secondary">
        Custom Python code
      </Typography>
      <IconButton aria-label="Close panel" onClick={onClose} size="small">
        <Iconify icon="solar:close-linear" />
      </IconButton>
    </Box>
    <Typography role="status" typography="s1" color="text.primary">
      {CODE_COLUMNS_DISABLED_REASON}
    </Typography>
    <Box>
      <Button variant="outlined" size="small" onClick={onClose}>
        Close
      </Button>
    </Box>
  </Box>
);

ExecuteCodeChild.propTypes = {
  onClose: PropTypes.func,
};

const ExecuteCode = () => {
  const { openExecuteCode, setOpenExecuteCode } = useExecuteCodeStore();

  const onClose = () => {
    setOpenExecuteCode(false);
  };

  const covered = useDrawerCovered(Boolean(openExecuteCode));
  return (
    <Drawer
      anchor="right"
      open={Boolean(openExecuteCode)}
      sx={coveredDrawerSx(covered)}
      onClose={onClose}
      variant="persistent"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 2,
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
      <ExecuteCodeChild onClose={onClose} />
    </Drawer>
  );
};

export default ExecuteCode;
