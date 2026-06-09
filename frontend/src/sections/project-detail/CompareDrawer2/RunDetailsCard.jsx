import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import {
  Box,
  IconButton,
  Tab,
  Tabs,
  Typography,
  useTheme,
} from "@mui/material";
import { ShowComponent } from "src/components/show";
import { JsonView } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";
import { copyToClipboard, getScorePercentage } from "src/utils/utils";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/components/traceDetailDrawer/DrawerRightRenderer/SpanAccordianElements";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { normalizeEvalCellValue } from "src/sections/develop-detail/DataTab/common";

const RunDetailsCard = ({ value, column, allowCopy = false }) => {
  const [tabValue, setTabValue] = useState("markdown");

  const dataType = column?.dataType;
  const theme = useTheme();

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
      // LLM evals may pass {score, choice} (object) or a Python-repr string.
      const normalized = normalizeEvalCellValue(value?.cellValue);
      const rawScore =
        normalized && typeof normalized === "object" && !Array.isArray(normalized)
          ? typeof normalized.score === "number"
            ? normalized.score
            : NaN
          : parseFloat(normalized);
      if (isNaN(rawScore)) return "";
      return `${getScorePercentage(rawScore * 10)}%`;
    }
    return value?.cellValue;
  }, [value?.cellValue, dataType]);

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
              border: "1px solid",
              borderColor: "action.selected",
              borderRadius: "8px",
            }}
          >
            <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <Tabs
                  textColor="primary"
                  value={tabValue}
                  onChange={handleTabChange}
                  TabIndicatorProps={{
                    style: { display: "none" },
                  }}
                  sx={{
                    minHeight: 32,
                    "& .MuiTab-root": {
                      minHeight: 32,
                      padding: "12px",
                      color: "divider",
                      fontWeight: theme.typography["fontWeightMedium"],
                      typography: "s1",
                      "&:not(:last-of-type)": {
                        marginRight: "4px",
                      },
                      "&.Mui-selected": {
                        color: theme.palette.primary.main,
                      },
                    },
                  }}
                >
                  <Tab value="markdown" label="Markdown" />
                  <Tab value="raw" label="Raw" />
                </Tabs>
                {allowCopy ? (
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
                        icon="basil:copy-outline"
                        sx={{ color: "text.disabled" }}
                      />
                    </IconButton>
                  </Box>
                ) : null}
              </Box>

              <ShowComponent condition={tabValue === "raw"}>
                <Box
                  sx={{
                    paddingX: "16px",
                    paddingY: "12px",
                    backgroundColor: "background.default",
                    overflowWrap: "break-word",
                  }}
                >
                  <Typography variant="body2">
                    {["array", "json"].includes(dataType) &&
                    isJson(formattedValue) ? (
                      <JsonView
                        data={JSON.parse(formattedValue)}
                        shouldExpandNode={() => true}
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
                    overflowWrap: "break-word",
                    backgroundColor: "background.default",
                  }}
                >
                  <Typography variant="body2">
                    <CellMarkdown spacing={0} text={formattedValue} />
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

RunDetailsCard.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
  allowCopy: PropTypes.bool,
};

export default RunDetailsCard;
