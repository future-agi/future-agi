import { Box, IconButton, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import CellMarkdown from "src/sections/common/CellMarkdown";
import JsonOutputRenderer from "./JsonOutputRenderer";
import { copyToClipboard, interpolateColorBasedOnScore } from "src/utils/utils";

/**
 * Check if a string is valid JSON object/array
 */
const isValidJson = (str) => {
  if (typeof str !== "string") return false;
  try {
    const parsed = JSON.parse(str);
    return typeof parsed === "object" && parsed !== null;
  } catch {
    return false;
  }
};

const ResultsCellRenderer = (props) => {
  const valueObject = props?.value;
  const column = props?.column?.colDef;
  const rowIndex = props?.node?.rowIndex;
  const isFailure = valueObject?.meta?.failure;
  const reason = valueObject?.meta?.reason;

  // Check if the output value is valid JSON (for row 0 - output row)
  const isJsonOutput = useMemo(() => {
    if (rowIndex !== 0) return false;
    return isValidJson(valueObject);
  }, [valueObject, rowIndex]);

  //   console.log("🚀 ~ ResultsCellRenderer ~ value, column:", {
  //     valueObject,
  //     column,
  //     props,
  //   });

  const renderEvaluationData = () => {
    if (valueObject?.output === "Pass/Fail") {
      return (
        <Typography fontSize="13px" color="text.secondary">
          {valueObject.value}
        </Typography>
      );
    }
    if (valueObject?.output === "score") {
      return (
        <Typography fontSize="13px" color="text.secondary">
          {valueObject?.value * 100}%
        </Typography>
      );
    }
    if (valueObject?.output === "choices") {
      return (
        <Typography fontSize="13px" color="text.primary">
          {valueObject.value.join(" · ")}
        </Typography>
      );
    }
  };

  const getBackgroundColor = () => {
    if (isFailure) {
      return "white";
    }
    if (valueObject?.output === "Pass/Fail") {
      return valueObject.value === "Passed"
        ? interpolateColorBasedOnScore(10)
        : interpolateColorBasedOnScore(0);
    }
    if (valueObject?.output === "score") {
      return interpolateColorBasedOnScore(valueObject.value, 1);
    }
  };

  if (column.field === "evaluation") {
    return (
      <Typography
        fontSize="13px"
        color="text.secondary"
        sx={{
          padding: 1,
          alignItems: "center",
          display: "flex",
          height: "100%",
        }}
      >
        {valueObject}
      </Typography>
    );
  }
  const handleCopyClick = (data) => {
    if (data) {
      copyToClipboard(data);
      enqueueSnackbar("Copied to clipboard", {
        variant: "success",
      });
    }
  };

  if (rowIndex === 0) {
    return (
      <Box
        sx={{
          whiteSpace: "pre-wrap",
          lineHeight: "2",
          overflow: "auto",
          display: "-webkit-box",
          WebkitBoxOrient: "vertical",
          padding: 1,
          height: "100%",
        }}
      >
        {!isJsonOutput && (
          <Tooltip title="Copy" arrow>
            <IconButton
              sx={{ position: "absolute", top: "0", right: "0" }}
              size="small"
              onClick={() => handleCopyClick(valueObject)}
            >
              <Iconify
                icon="solar:copy-linear"
                sx={{ color: "text.disabled" }}
              />
            </IconButton>
          </Tooltip>
        )}
        {isJsonOutput ? (
          <JsonOutputRenderer
            data={valueObject}
            columnName="output"
            showPaths={true}
            initialExpanded={true}
          />
        ) : (
          <CellMarkdown spacing={0} text={valueObject} />
        )}
      </Box>
    );
  }

  // below this is eval code

  if (isFailure) {
    return (
      <CustomTooltip show title={reason} placement="bottom" arrow>
        <Box
          sx={{
            flex: 1,
            height: "100%",
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <Typography fontSize="13px" color="error.main">
            Error
          </Typography>
        </Box>
      </CustomTooltip>
    );
  }

  return (
    <CustomTooltip show title={reason} placement="bottom" arrow>
      <Box
        sx={{
          flex: 1,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          backgroundColor: getBackgroundColor(),
        }}
      >
        <Box
          sx={{
            flex: 1,
            paddingY: 3,
            paddingX: 2,
            backgroundColor: getBackgroundColor(),
          }}
        >
          {renderEvaluationData()}
        </Box>
        {/* {renderMeta()} */}
      </Box>
    </CustomTooltip>
  );
};

ResultsCellRenderer.propTypes = {
  column: PropTypes.object,
  data: PropTypes.object,
  value: PropTypes.any,
  node: PropTypes.object,
};

export default ResultsCellRenderer;
