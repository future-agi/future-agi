import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import { trackEvent, Events } from "src/utils/Mixpanel";
import logger from "src/utils/logger";
import CellMarkdown from "src/sections/common/CellMarkdown";

const GenerateResult = ({
  resultState,
  output,
  showUtils,
  onClose,
  enabled,
  loading,
  hideInitialText,
  isApplyButtonEnabled,
  onApply,
}) => {
  const [outputs, setOutputs] = useState([]);
  const [currentIndex, setCurrentIndex] = useState(0);

  React.useEffect(() => {
    if (output) {
      setOutputs((prev) => [...prev, output]);
      setCurrentIndex(outputs.length);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [output]);

  const handleCopy = async (output) => {
    trackEvent(Events.generatedPromptCopied);
    try {
      await navigator.clipboard.writeText(output);
      enqueueSnackbar("Copied to clipboard!", {
        variant: "success",
      });
    } catch (error) {
      logger.error("Failed to copy text: ", error);
      enqueueSnackbar("Failed to copy text.", {
        variant: "error",
      });
    }
  };

  const handlePrevious = () => {
    trackEvent(Events.prevGeneratedPrompt);
    if (currentIndex > 0) {
      let newIndex = currentIndex - 1;
      while (newIndex >= 0 && outputs[newIndex] === "Generating...") {
        newIndex--;
      }
      if (newIndex >= 0) {
        setCurrentIndex(newIndex);
      }
    }
  };

  const handleNext = () => {
    trackEvent(Events.nextGeneratedPrompt);
    if (currentIndex < outputs.length - 1) {
      let newIndex = currentIndex + 1;
      while (
        newIndex < outputs.length &&
        outputs[newIndex] === "Generating..."
      ) {
        newIndex++;
      }
      if (newIndex < outputs.length) {
        setCurrentIndex(newIndex);
      }
    }
  };

  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        // padding: "17px 17px 10px",
        paddingLeft: "0px",
        gap: "3px",
        ...(hideInitialText && { padding: "0 10px" }),
      }}
    >
      <Box display="flex" flexDirection="column" gap="13px" height="100%">
        <Box
          display={"flex"}
          flexDirection={"row"}
          justifyContent={"space-between"}
          alignItems={"center"}
        >
          <Typography
            variant="subheader"
            color="text.disabled"
            fontSize="0.875rem"
            marginY="5px"
          >
            Results
          </Typography>
          {showUtils ? (
            <Box sx={{ display: "flex", gap: 0.4, alignItems: "center" }}>
              <Tooltip title="See previous result" arrow>
                <IconButton
                  sx={{
                    marginTop: "4px",
                  }}
                  disabled={enabled}
                  onClick={handlePrevious}
                >
                  <Iconify
                    icon="ci:arrow-undo-down-left"
                    color="text.disabled"
                  />
                </IconButton>
              </Tooltip>
              <Tooltip title="See latest Result" arrow>
                <IconButton disabled={enabled} onClick={handleNext}>
                  <Iconify
                    icon="ci:arrow-undo-up-right"
                    color="text.disabled"
                  />
                </IconButton>
              </Tooltip>
              <Tooltip title="Copy" arrow>
                <IconButton
                  onClick={() => handleCopy(outputs[currentIndex])}
                  disabled={
                    outputs[currentIndex] === "Generating..." ||
                    output[currentIndex] === undefined
                  }
                >
                  <Iconify icon="solar:copy-linear" color="text.disabled" />
                </IconButton>
              </Tooltip>
              <Button
                size="small"
                disabled={!isApplyButtonEnabled}
                variant="contained"
                color="primary"
                onClick={() => {
                  onApply(outputs[currentIndex]);
                  // onClose()
                }}
              >
                Apply
              </Button>
              <IconButton onClick={onClose}>
                <Iconify icon="solar:close-linear" />
              </IconButton>
            </Box>
          ) : null}
        </Box>

        <Box
          sx={{
            border: "2px solid var(--border-light)",
            borderRadius: "8px",
            backgroundColor: "action.hover",
            padding: "11px 16px 16px 20px",
            flex: "1",
            overflow: "auto",
            maxHeight: "95%",
            position: "relative",
            fontSize: "0.8125rem",
          }}
        >
          {loading ? (
            <>
              <Typography
                variant="subtitle4"
                sx={{
                  color: "text.disabled",
                  fontWeight: "500",
                }}
              >
                {outputs[currentIndex]}
              </Typography>
            </>
          ) : (
            <>
              <CellMarkdown spacing={0} text={outputs[currentIndex]} />
            </>
          )}

          {!hideInitialText && resultState ? (
            <Typography
              variant="subtitle2"
              color={
                resultState === "Completed" ? "text.primary" : "text.secondary"
              }
              fontWeight={"fontWeightRegular"}
            />
          ) : null}
        </Box>
      </Box>

      {/* <ConfirmDialog
        content="Are you sure you want to proceed?"
        action={<Button variant="contained" color="error" onClick={confirmClose}>Confirm</Button>}
        open={isConfirmModalOpen}
        onClose={closeConfirmModal}
        onConfirm={confirmClose}
        title="Confirm Action"
        message="Are you sure you want to proceed?
        This action can cause your data lost."
      /> */}
    </Box>
  );
};

GenerateResult.propTypes = {
  hideInitialText: PropTypes.bool.isRequired,
  resultState: PropTypes.any,
  output: PropTypes.string,
  showUtils: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  enabled: PropTypes.bool,
  loading: PropTypes.bool,
  isApplyButtonEnabled: PropTypes.bool.isRequired,
  onApply: PropTypes.func.isRequired,
};

GenerateResult.defaultProps = {
  hideInitialText: false,
  output: "",
};

export default GenerateResult;
