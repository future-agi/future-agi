import React from "react";
import {
  Box,
  Divider,
  IconButton,
  LinearProgress,
  Typography,
} from "@mui/material";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import Iconify from "../iconify";
import PropTypes from "prop-types";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import CustomJsonViewer from "../custom-json-viewer/CustomJsonViewer";

import "./jsonStyle.css";

const BottomAttributesTab = ({ observationSpan, isLoading, sx = {} }) => {
  if (isLoading) return <LinearProgress />;
  if (!observationSpan) {
    return (
      <Box
        sx={{
          padding: 2,
          height: "200px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography typography="s2_1" fontWeight="fontWeightMedium">
          Attributes are Not Available
        </Typography>
      </Box>
    );
  }
  return (
    <Accordion defaultExpanded>
      <AccordionSummary
        sx={{
          fontSize: "16px",
          fontWeight: "600",
          color: "grey",
        }}
      >
        <IconButton
          onClick={(event) => {
            event.stopPropagation();
            copyToClipboard(observationSpan);
            enqueueSnackbar("Copied to clipboard", {
              variant: "success",
            });
          }}
          sx={{
            position: "absolute",
            top: 8,
            right: 8,
            zIndex: 1,
          }}
        >
          <Iconify icon="solar:copy-linear" color="grey" width={20} />
        </IconButton>
        All Attributes
      </AccordionSummary>
      <Divider />
      <AccordionDetails>
        <Box
          sx={{
            backgroundColor: "background.paper",
            fontSize: "13px",
            whiteSpace: "pre-wrap",
            maxHeight: "72vh",
            overflow: "auto",
            position: "relative",
            minWidth: "100%",
            ...sx,
          }}
        >
          <CustomJsonViewer
            object={observationSpan}
            searchable
            searchPlaceholder="Search attributes..."
          />
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

BottomAttributesTab.propTypes = {
  observationSpan: PropTypes.object,
  isLoading: PropTypes.bool,
  sx: PropTypes.object,
};

export default BottomAttributesTab;
