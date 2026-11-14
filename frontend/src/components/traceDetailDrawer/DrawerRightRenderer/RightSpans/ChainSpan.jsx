import React, { useState } from "react";
import { SegmentedTabs } from "src/components/tabs/tabs";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../SpanAccordianElements";
import { Box, IconButton, Tab, Typography } from "@mui/material";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { useNavigate } from "react-router";

const ChainSpan = ({
  value,
  column,
  allowCopy = false,
  promptName,
  promptTemplateId,
}) => {
  const navigate = useNavigate();
  const [tabValue, setTabValue] = useState("raw");
  const handleTabChange = (_, newValue) => {
    setTabValue(newValue);
  };

  const handleClick = () => {
    if (!promptTemplateId) return;

    navigate(`/dashboard/workbench/create/${promptTemplateId}?tab=Metrics`);
  };

  return (
    <Accordion defaultExpanded disableGutters>
      <AccordionSummary>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          {column?.headerName}{" "}
          <ShowComponent condition={promptName && promptTemplateId}>
            <Box
              onClick={handleClick}
              sx={{
                display: "flex",
                alignItems: "center",
                paddingX: "8px",
                paddingY: "2px",
                borderRadius: (theme) => theme.spacing(1),
                whiteSpace: "nowrap",
                border: "1px solid",
                borderColor: "primary.light",
                backgroundColor: "action.hover",
                gap: (theme) => theme.spacing(1),
              }}
            >
              <Typography
                typography="s2"
                color="primary.dark"
                sx={{
                  maxWidth: "300px",
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                }}
              >
                Prompt : {promptName}
              </Typography>
            </Box>
          </ShowComponent>
        </Box>
      </AccordionSummary>
      <AccordionDetails
        sx={{
          paddingY: (theme) => theme.spacing(1),
          paddingX: (theme) => theme.spacing(0),
        }}
      >
        <Box
          sx={{
            paddingX: (theme) => theme.spacing(2),
            paddingBottom: (theme) => theme.spacing(1),
          }}
        >
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: (theme) => theme.spacing(1),
            }}
          >
            <Box
              sx={{
                borderBottom: (theme) => theme.spacing(1),
                borderColor: "divider",
                position: "relative",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <SegmentedTabs value={tabValue} onChange={handleTabChange}>
                  <Tab value="markdown" label="Markdown" />
                  <Tab value="raw" label="Raw" />
                </SegmentedTabs>
                {allowCopy && (
                  <Box>
                    <IconButton
                      onClick={() => {
                        copyToClipboard(value?.cellValue);
                        enqueueSnackbar("Copied to clipboard", {
                          variant: "success",
                        });
                      }}
                    >
                      <Iconify
                        icon="solar:copy-linear"
                        color="grey"
                        width={20}
                        sx={{ color: "text.secondary" }}
                      />
                    </IconButton>
                  </Box>
                )}
              </Box>

              <ShowComponent condition={tabValue === "raw"}>
                <Box
                  sx={{
                    paddingX: (theme) => theme.spacing(2),
                    paddingY: (theme) => theme.spacing(1.5),
                    overflowWrap: "break-word",
                    // backgroundColor: "background.neutral",
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography typography="s1">
                    <CustomJsonViewer object={value?.cellValue} />
                  </Typography>
                </Box>
              </ShowComponent>
              <ShowComponent condition={tabValue === "markdown"}>
                <Box
                  sx={{
                    paddingX: (theme) => theme.spacing(2),
                    paddingY: (theme) => theme.spacing(1.5),
                    overflowWrap: "break-word",
                    // backgroundColor: "background.neutral",
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography typography="s1">
                    <CellMarkdown
                      spacing={0}
                      text={JSON.stringify(value?.cellValue)}
                    />
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

ChainSpan.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
  allowCopy: PropTypes.bool,
  showBox: PropTypes.bool,
  promptName: PropTypes.string,
  promptTemplateId: PropTypes.string,
};

export default ChainSpan;
