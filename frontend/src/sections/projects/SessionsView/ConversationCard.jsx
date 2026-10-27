import React, { useMemo, useState, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Typography, IconButton, Button } from "@mui/material";
import { ShowComponent } from "src/components/show";
import { allExpanded, defaultStyles } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";
import { getScorePercentage, copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import CellMarkdown from "src/sections/common/CellMarkdown";
import SvgColor from "src/components/svg-color";
import { useTraceDrawerStore } from "../TracesDrawer/useTraceDrawerStore";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";

export const CONVERSATION_CARD_CONTENT_SX = {
  width: "100%",
  maxWidth: "40vw",
  minWidth: 0,
  overflow: "hidden",
};

const ConversationCard = ({ value, column }) => {
  const { viewType: tabValue } = useTraceDrawerStore();
  const [isExpanded, setIsExpanded] = useState(false);
  const markdownRef = useRef(null);

  const dataType = column?.dataType;

  // Dummy indices for color coding - array of [start, end] pairs
  // const highlightIndices = [];

  const isJson = (v) => {
    if (typeof v === "object" && v !== null) {
      return true;
    }

    if (typeof v !== "string") {
      return false;
    }

    try {
      const parsed = JSON.parse(v);

      return typeof parsed === "object" && parsed !== null;
    } catch (e) {
      return false;
    }
  };

  const rawValue = useMemo(() => {
    const cellValue = value?.cellValue;

    if (cellValue === null) return "null";

    if (typeof cellValue === "object") {
      try {
        return JSON.stringify(cellValue, null, 2);
      } catch (e) {
        return String(cellValue);
      }
    }

    return String(cellValue);
  }, [value?.cellValue]);

  const formattedValue = useMemo(() => {
    if (dataType === "float") {
      const numValue = parseFloat(value?.cellValue);
      if (isNaN(numValue)) return rawValue;
      return `${getScorePercentage(numValue * 10)}%`;
    }
    return rawValue;
  }, [rawValue, dataType, value?.cellValue]);

  const contentToCopy = useMemo(() => {
    if (tabValue === "markdown") {
      return formattedValue;
    }

    if (["array", "object"].includes(dataType) && isJson(formattedValue)) {
      try {
        const parsed =
          typeof formattedValue === "string"
            ? JSON.parse(formattedValue)
            : formattedValue;
        return JSON.stringify(parsed, null, 2);
      } catch (e) {
        return formattedValue;
      }
    }

    return formattedValue;
  }, [tabValue, formattedValue, dataType]);

  const wordCount = useMemo(() => {
    if (typeof formattedValue === "string") {
      const trimmed = formattedValue.trim();
      if (!trimmed) return 0;
      return trimmed.split(/\s+/).length;
    }
    return 0;
  }, [formattedValue]);

  const shouldTruncate = wordCount > 100 && !isExpanded;

  const displayValue = useMemo(() => {
    if (shouldTruncate && typeof formattedValue === "string") {
      const words = formattedValue.trim().split(/\s+/);
      return words.slice(0, 100).join(" ") + "...";
    }
    return formattedValue;
  }, [formattedValue, shouldTruncate]);

  const isJsonContent = useMemo(() => {
    return ["array", "object"].includes(dataType) && isJson(formattedValue);
  }, [dataType, formattedValue]);

  const renderContent = () => {
    if (isJsonContent) {
      try {
        const parsed =
          typeof formattedValue === "string"
            ? JSON.parse(formattedValue)
            : formattedValue;

        return (
          <CustomJsonViewer
            object={parsed}
            shouldExpandNode={allExpanded}
            clickToExpandNode={true}
            style={defaultStyles}
          />
        );
      } catch (e) {
        return displayValue;
      }
    }

    return displayValue;
  };

  return (
    <Box sx={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
      {/* Copy Button */}
      <IconButton
        onClick={() => {
          copyToClipboard(contentToCopy);
          enqueueSnackbar("Copied to clipboard", {
            variant: "success",
          });
        }}
        sx={{
          position: "absolute",
          top: 12,
          right: 12,
          zIndex: 2,
          backgroundColor: "background.paper",
          color: "text.primary",
          "&:hover": {
            backgroundColor: "background.neutral",
          },
        }}
        size="small"
      >
        <SvgColor
          src={"/assets/icons/ic_copy.svg"}
          sx={{
            height: "16px",
            width: "16px",
          }}
        />
      </IconButton>

      <Box sx={CONVERSATION_CARD_CONTENT_SX}>
        <ShowComponent condition={tabValue === "raw"}>
          <Box sx={{ minWidth: 0 }}>
            <Typography
              variant="body2"
              sx={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                overflowWrap: "anywhere",
              }}
            >
              {renderContent()}
            </Typography>
            {!isJsonContent && wordCount > 100 && (
              <Button
                variant="text"
                onClick={() => setIsExpanded(!isExpanded)}
                sx={{
                  ml: 0.5,
                  p: 0,
                  minWidth: "auto",
                  textTransform: "none",
                  textDecoration: "underline",
                  verticalAlign: "baseline",
                  display: "inline",
                  fontWeight: "fontWeightRegular !important",
                  typography: "body2",
                  "&:hover": { textDecoration: "underline" },
                }}
                size="small"
              >
                {isExpanded ? "See less" : "See more"}
              </Button>
            )}
          </Box>
        </ShowComponent>

        <ShowComponent condition={tabValue === "markdown"}>
          <Box
            ref={markdownRef}
            sx={{
              minWidth: 0,
              overflowWrap: "anywhere",
              "& *": {
                maxWidth: "100%",
              },
            }}
          >
            {isJsonContent ? (
              // JSON → render JSON viewer
              <CustomJsonViewer
                object={
                  typeof formattedValue === "string"
                    ? JSON.parse(formattedValue)
                    : formattedValue
                }
                shouldExpandNode={allExpanded}
                clickToExpandNode={true}
                style={defaultStyles}
              />
            ) : (
              // NOT JSON → render cell markdown
              <CellMarkdown spacing={0} text={displayValue} />
            )}

            {!isJsonContent && wordCount > 100 && (
              <Button
                variant="text"
                onClick={() => setIsExpanded(!isExpanded)}
                sx={{
                  ml: 0.5,
                  p: 0,
                  minWidth: "auto",
                  textTransform: "none",
                  textDecoration: "underline",
                  verticalAlign: "baseline",
                  display: "inline",
                  fontWeight: "fontWeightRegular !important",
                  typography: "body2",
                  "&:hover": { textDecoration: "underline" },
                }}
                size="small"
              >
                {isExpanded ? "See less" : "See more"}
              </Button>
            )}
          </Box>
        </ShowComponent>
      </Box>
    </Box>
  );
};

ConversationCard.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
};

export default ConversationCard;
