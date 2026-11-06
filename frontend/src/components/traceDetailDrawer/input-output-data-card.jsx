import PropTypes from "prop-types";
import { SegmentedTabs } from "src/components/tabs/tabs";
import React, { useMemo, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import { Box, Tab, Typography, IconButton } from "@mui/material";
import { ShowComponent } from "src/components/show";
import Markdown from "react-markdown";
import { JsonView, allExpanded, defaultStyles } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";
import { getScorePercentage } from "src/utils/utils";
import Iconify from "../iconify";
import logger from "src/utils/logger";

const InputOutputDataCard = ({ value, column }) => {
  const [tabValue, setTabValue] = useState("markdown");

  const dataType = column?.dataType;

  const handleTabChange = (event, newValue) => {
    setTabValue(newValue);
  };

  const isJson = (v) => {
    try {
      JSON.parse(v);
      return true;
    } catch (e) {
      return false;
    }
  };

  const formattedValue = useMemo(() => {
    if (dataType === "float") {
      return `${getScorePercentage(parseFloat(value?.cellValue) * 10)}%`;
    }
    return value?.cellValue;
  }, [value?.cellValue, dataType]);

  const handleCopy = () => {
    const contentToCopy =
      tabValue === "markdown"
        ? formattedValue
        : ["array", "json"].includes(dataType) && isJson(formattedValue)
          ? JSON.stringify(JSON.parse(formattedValue), null, 2)
          : formattedValue;

    navigator.clipboard.writeText(contentToCopy).then(() => {
      logger.debug("Content copied to clipboard");
    });
  };

  return (
    <Accordion defaultExpanded disableGutters>
      <AccordionSummary>{column?.headerName}</AccordionSummary>
      <AccordionDetails sx={{ padding: 0 }}>
        <Box
          sx={{
            paddingX: 2,
            paddingBottom: 1,
          }}
        >
          <Box
            sx={{
              position: "relative",
              border: "1px solid",
              borderColor: "action.selected",
              borderRadius: "8px",
            }}
          >
            {/* Copy Button */}
            <IconButton
              onClick={handleCopy}
              sx={{
                position: "absolute",
                top: 8,
                right: 16,
                zIndex: 2,
                backgroundColor: "background.paper",
                "&:hover": {
                  backgroundColor: "background.neutral",
                },
              }}
              size="small"
            >
              <Iconify icon="solar:copy-linear" color="grey" width={20} />
            </IconButton>
            <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
              <SegmentedTabs value={tabValue} onChange={handleTabChange}>
                <Tab value="markdown" label="Markdown" />
                <Tab value="raw" label="Raw" />
              </SegmentedTabs>
              <ShowComponent condition={tabValue === "raw"}>
                <Box
                  sx={{
                    paddingX: "16px",
                    paddingY: "12px",
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography variant="body2">
                    {["array", "json"].includes(dataType) &&
                    isJson(formattedValue) ? (
                      <JsonView
                        data={JSON.parse(formattedValue)}
                        shouldExpandNode={allExpanded}
                        clickToExpandNode={true}
                        style={defaultStyles}
                      />
                    ) : (
                      formattedValue
                    )}
                  </Typography>
                </Box>
              </ShowComponent>
              <ShowComponent condition={tabValue === "markdown"}>
                <Box
                  sx={{
                    paddingX: "16px",
                    paddingY: "12px",
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography variant="body2">
                    <Markdown>{formattedValue}</Markdown>
                  </Typography>
                </Box>
              </ShowComponent>
            </Box>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

InputOutputDataCard.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
};

export default InputOutputDataCard;
