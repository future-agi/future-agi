import { Box, Button } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { LoadingButton } from "@mui/lab";

const BottomButtons = ({
  onCancelClick,
  onTestClick,
  onNextClick,
  isCancelDisable,
  isTestDisabled,
  isNextDisabled,
  nextLoading,
  testLoading,
  onBackClick,
  isBackDisabled,
  nextButtonText,
}) => {
  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        display: "flex",
        justifyContent: "flex-end",
        gap: "12px",
        paddingBottom: "23px",
        paddingX: "52px",
        position: "sticky",
        bottom: 0,
      }}
    >
      {onCancelClick ? (
        <Button
          onClick={onCancelClick}
          color="inherit"
          variant="outlined"
          size="small"
          disabled={isCancelDisable}
        >
          Cancel
        </Button>
      ) : null}
      {onTestClick ? (
        <LoadingButton
          onClick={onTestClick}
          color="primary"
          variant="outlined"
          size="small"
          disabled={isTestDisabled}
          loading={testLoading}
        >
          Test
        </LoadingButton>
      ) : null}
      {onBackClick ? (
        <LoadingButton
          onClick={onBackClick}
          color="primary"
          variant="outlined"
          size="small"
          disabled={isBackDisabled}
        >
          Back
        </LoadingButton>
      ) : null}
      {onNextClick ? (
        <LoadingButton
          onClick={onNextClick}
          color="primary"
          variant="contained"
          size="small"
          disabled={isNextDisabled}
          loading={nextLoading}
        >
          {nextButtonText ? nextButtonText : "Next"}
        </LoadingButton>
      ) : null}
    </Box>
  );
};

BottomButtons.propTypes = {
  onCancelClick: PropTypes.func,
  onTestClick: PropTypes.func,
  onNextClick: PropTypes.func,
  isCancelDisable: PropTypes.bool,
  isTestDisabled: PropTypes.bool,
  isNextDisabled: PropTypes.bool,
  nextLoading: PropTypes.bool,
  testLoading: PropTypes.bool,
  nextButtonText: PropTypes.string,
  onBackClick: PropTypes.func,
  isBackDisabled: PropTypes.bool,
};

export default BottomButtons;
