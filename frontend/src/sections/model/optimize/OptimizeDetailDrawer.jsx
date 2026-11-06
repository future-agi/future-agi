import { Box, Drawer, IconButton, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import DetailItem from "src/components/drawer-detail-item/drawer-detail-item";
import CircularProgressWithLabel from "src/components/circular-progress-with-label/CircularProgressWithLabel";
import {
  getScorePercentage,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const OptimizeDetailDrawer = ({ onClose, open, columns, selectedRow }) => {
  const renderContent = (content) => {
    if (typeof content === "string") {
      return <Typography variant="body2">{content}</Typography>;
    }

    if (typeof content === "number") {
      return (
        <CircularProgressWithLabel
          color={interpolateColorBasedOnScore(content)}
          value={getScorePercentage(content)}
        />
      );
    }
  };

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
          width: "480px",
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
      <IconButton
        onClick={onClose}
        sx={{ position: "absolute", top: "12px", right: "12px" }}
      >
        <Iconify icon="solar:close-linear" />
      </IconButton>
      <Box sx={{ padding: "20px" }}>
        <Box>
          <Typography variant="body2" color="text.disabled">
            Datapoints info
          </Typography>
          <Iconify />
        </Box>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 3.75,
            marginTop: 3.75,
          }}
        >
          {columns?.map(({ label, value }) => (
            <Box key={value}>
              <DetailItem
                title={label}
                content={renderContent(selectedRow?.[value])}
              />
            </Box>
          ))}
        </Box>
      </Box>
    </Drawer>
  );
};

OptimizeDetailDrawer.propTypes = {
  onClose: PropTypes.func,
  open: PropTypes.bool,
  columns: PropTypes.array,
  selectedRow: PropTypes.object,
};

export default OptimizeDetailDrawer;
