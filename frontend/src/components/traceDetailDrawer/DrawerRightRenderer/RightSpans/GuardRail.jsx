import React, { useMemo, useState } from "react";
import { SegmentedTabs } from "src/components/tabs/tabs";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../SpanAccordianElements";
import { Box, IconButton, Tab, Typography } from "@mui/material";
import { copyToClipboard, getScorePercentage } from "src/utils/utils";
import { JsonView, allExpanded } from "react-json-view-lite";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { useNavigate } from "react-router";

const GuardRail = ({
  value,
  column,
  allowCopy = false,
  rightRuleset = null,
  guardConfig = false,
  promptName,
  promptTemplateId,
}) => {
  const [tabValue, setTabValue] = useState("raw");
  const navigate = useNavigate();
  const dataType = column?.dataType;

  const isJson = (v) => {
    try {
      JSON.parse(v);
      return true;
    } catch (e) {
      return false;
    }
  };

  const handleTabChange = (_, newValue) => {
    setTabValue(newValue);
  };

  const formattedValue = useMemo(() => {
    if (dataType === "float") {
      return `${getScorePercentage(parseFloat(value?.cellValue) * 10)}%`;
    }
    return value?.cellValue;
  }, [value?.cellValue, dataType]);

  const handleClick = () => {
    if (!promptTemplateId) return;

    navigate(`/dashboard/workbench/create/${promptTemplateId}?tab=Metrics`);
  };

  if (guardConfig && isJson(formattedValue)) {
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
        <AccordionDetails sx={{ paddingY: 1, paddingX: 0 }}>
          <Box
            sx={{
              paddingX: 2,
              paddingBottom: 1,
            }}
          >
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
              }}
            >
              <Box
                sx={{
                  padding: "5px 16px",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  typography="s2"
                  sx={{ color: "primary.main", fontWeight: 600 }}
                >
                  JSON
                </Typography>
              </Box>
              <Box
                sx={{
                  padding: "16px",
                }}
              >
                <Typography typography="s1">
                  <JsonView
                    data={JSON.parse(formattedValue)}
                    shouldExpandNode={allExpanded}
                    clickToExpandNode={false}
                    style={{
                      container: "attributesJsonContainer",
                      basicChildStyle: "attributesJsonChild",
                      label: "attributesLabel",
                      clickableLabel: "attributesClickableLabel",
                      nullValue: "attributesNullValue",
                      undefinedValue: "attributesUndefinedValue",
                      numberValue: "attributesNumberValue",
                      stringValue: "attributesStringValue",
                      booleanValue: "attributesBooleanValue",
                      otherValue: "attributesOtherValue",
                      punctuation: "attributesPunctuation",
                      expandIcon: "customExpandIcon",
                      collapseIcon: "customCollapseIcon",
                      collapsedContent: "customCollapsedContent",
                    }}
                  />
                </Typography>
              </Box>
            </Box>
          </Box>
          {/* copy button */}
          <Box
            sx={{
              position: "absolute",
              top: 57,
              right: 25,
            }}
          >
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
        </AccordionDetails>
      </Accordion>
    );
  }

  // Regular rendering for non-guardConfig cases
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
      <AccordionDetails sx={{ paddingY: 1, paddingX: 0 }}>
        <Box
          sx={{
            paddingX: 2,
            paddingBottom: 1,
          }}
        >
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          >
            <Box
              sx={{
                borderBottom: 1,
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
                  <Box
                    sx={{
                      position: "absolute",
                      top: 0,
                      right: 10,
                    }}
                  >
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
                    paddingX: "16px",
                    paddingY: "12px",
                    overflowWrap: "break-word",
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography typography="s1">{formattedValue}</Typography>
                </Box>
              </ShowComponent>
              <ShowComponent condition={tabValue === "markdown"}>
                <Box
                  sx={{
                    paddingX: "16px",
                    paddingY: "12px",
                    overflowWrap: "break-word",
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography typography="s1">
                    <CellMarkdown spacing={0} text={formattedValue} />
                  </Typography>
                </Box>
              </ShowComponent>
            </Box>
          </Box>
        </Box>
        {rightRuleset && (
          <Box
            sx={{
              position: "absolute",
              top: "13px",
              right: "18px",
              height: "30px",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 1,
              backgroundColor: "status.danger.bg",
              paddingX: 1.3,
              borderRadius: "4px",
            }}
          >
            <Iconify
              icon="solar:danger-triangle-linear"
              height={20}
              width={20}
              color="status.danger.fg"
            />
            <Typography
              typography="s1"
              sx={{ color: "status.danger.fg" }}
            >{`${rightRuleset} - Triggered`}</Typography>
          </Box>
        )}
      </AccordionDetails>
    </Accordion>
  );
};

GuardRail.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
  allowCopy: PropTypes.bool,
  showBox: PropTypes.bool,
  rightRuleset: PropTypes.string,
  guardConfig: PropTypes.bool,
  promptName: PropTypes.string,
  promptTemplateId: PropTypes.string,
};

export default GuardRail;
