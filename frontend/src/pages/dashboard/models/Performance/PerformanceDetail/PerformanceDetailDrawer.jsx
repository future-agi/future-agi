import { Drawer, IconButton } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

import _ from "lodash";
import ChatEvalDetails from "./ChatEvalDetails";
import ContextEvalDetails from "./ContextEvalDetails";
import PromptTemplateEvalDetails from "./PromptTemplateEvalDetails";

const PerformanceDetailDrawer = ({
  evalType,
  open,
  onClose,
  performanceDetails,
  setSelectedImages,
}) => {
  const onImageClick = (curUrl) => {
    const images = [];
    let defaultIdx = 0;
    performanceDetails.modelInput
      ?.filter((o) => o["url"] !== undefined)
      ?.forEach((url) => {
        if (curUrl === url.url) defaultIdx = images.length;
        images.push({ src: url.url });
      });
    performanceDetails.modelOutput
      ?.filter((o) => o["url"] !== undefined)
      ?.forEach((url) => {
        if (curUrl === url.url) defaultIdx = images.length;
        images.push({ src: url.url });
      });
    setSelectedImages({ images, defaultIdx });
  };

  const renderDetails = () => {
    if (evalType === "EVALUATE_CHAT")
      return (
        <ChatEvalDetails
          performanceDetails={performanceDetails}
          onImageClick={onImageClick}
        />
      );
    else if (
      evalType === "EVALUATE_CONTEXT" ||
      evalType === "EVALUATE_CONTEXT_RANKING"
    ) {
      return (
        <ContextEvalDetails
          performanceDetails={performanceDetails}
          onImageClick={onImageClick}
        />
      );
    } else if (evalType === "EVALUATE_PROMPT_TEMPLATE") {
      return (
        <PromptTemplateEvalDetails
          performanceDetails={performanceDetails}
          onImageClick={onImageClick}
        />
      );
    }
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "550px",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "10px",
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
        onClick={() => onClose()}
        sx={{ position: "absolute", top: "12px", right: "12px" }}
      >
        <Iconify icon="mingcute:close-line" />
      </IconButton>
      {renderDetails()}
    </Drawer>
  );
};

PerformanceDetailDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  performanceDetails: PropTypes.object,
  isContextEval: PropTypes.bool,
  evalType: PropTypes.string,
  setSelectedImages: PropTypes.func,
};

export default PerformanceDetailDrawer;
