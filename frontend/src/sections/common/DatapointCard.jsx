import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../develop-detail/AccordianElements";
import { Box, Chip, Tab, Tabs, Typography, useTheme } from "@mui/material";
import { ShowComponent } from "src/components/show";
import "react-json-view-lite/dist/index.css";
import {
  copyToClipboard,
  extractAllThoughts,
  getScorePercentage,
  isJsonValue,
} from "src/utils/utils";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { enqueueSnackbar } from "src/components/snackbar";
import GenerateDiffText from "./GenerateDiffText";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import CellMarkdown from "./CellMarkdown";
import CustomTooltip from "src/components/tooltip";
import {
  parseAnnotationValue,
  renderAnnotationValue,
} from "./DevelopCellRenderer/AnnotationCellRenderer/renderAnnotationValue";
const DatapointCard = ({
  value,
  column,
  allowCopy = false,
  onDiffClick,
  showDiff,
  activeTab = "",
  sx = {},
  isEmptyField = false,
  indColsDifTracker,
  showTabs = true,
  isAgentsFinalNode,
}) => {
  const theme = useTheme();
  const [tabValue, setTabValue] = useState("markdown");
  const shouldApplyDefaultSx = Object.keys(sx).length === 0;
  useEffect(() => {
    // changes tab to "raw" if current tab is "difference" and showDiff is off and none of the diff tracker is on
    // showDiff is the state for a single datapoint card whether to show difference tab or not
    if (
      tabValue === "difference" &&
      !showDiff &&
      (Object.keys(indColsDifTracker).length === 0 ||
        Object.values(indColsDifTracker).every((v) => v === false))
    ) {
      setTabValue("raw");
      return;
    }
    // this to change tab from outside switches b/w "raw" & "difference"
    if (activeTab) {
      setTabValue(activeTab);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);
  const dataType = column?.dataType;
  const handleTabChange = (event, newValue) => {
    if (onDiffClick) {
      onDiffClick(newValue);
    }
    setTabValue(newValue);
  };
  const iconStyle = {
    color: "text.secondary",
  };
  const renderIcon = () => {
    if (column?.originType === "run_prompt") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_run_prompt.svg`}
          sx={{ width: 20, height: 20, color: "info.main" }}
        />
      );
    } else if (column?.originType === "evaluation") {
      return (
        <Iconify
          icon="material-symbols:check-circle-outline"
          sx={{ color: "info.success" }}
        />
      );
    } else if (
      column?.originType === "optimisation" ||
      column?.originType === "optimisation_evaluation"
    ) {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_optimize.svg`}
          sx={{ width: 20, height: 20, color: "primary.main" }}
        />
      );
    } else if (column?.originType === "annotation_label") {
      return <Iconify icon="jam:write" sx={iconStyle} />;
    } else if (dataType === "text") {
      return <Iconify icon="material-symbols:notes" sx={iconStyle} />;
    } else if (dataType === "array") {
      return <Iconify icon="material-symbols:data-array" sx={iconStyle} />;
    } else if (dataType === "integer") {
      return <Iconify icon="material-symbols:tag" sx={iconStyle} />;
    } else if (dataType === "float") {
      return <Iconify icon="tabler:decimal" sx={iconStyle} />;
    } else if (dataType === "boolean") {
      return (
        <Iconify icon="material-symbols:toggle-on-outline" sx={iconStyle} />
      );
    } else if (dataType === "datetime") {
      return <Iconify icon="tabler:calendar" sx={iconStyle} />;
    } else if (dataType === "json") {
      return <Iconify icon="material-symbols:data-object" sx={iconStyle} />;
    } else if (dataType === "image") {
      return <Iconify icon="material-symbols:image-outline" sx={iconStyle} />;
    } else if (dataType === "images") {
      return (
        <Iconify icon="material-symbols:art-track-outline" sx={iconStyle} />
      );
    }
  };

  const thoughts = useMemo(() => {
    if (value?.valueInfos) {
      const infoSources = [
        typeof value.valueInfos === "string" ? value.valueInfos : null,
        typeof value.valueInfos?.reason === "string"
          ? value.valueInfos.reason
          : null,
      ].filter(Boolean);
      for (const src of infoSources) {
        const extracted = extractAllThoughts(src);
        if (extracted.length > 0) return extracted;
      }
    }
    return null;
  }, [value?.valueInfos]);

  const isAnnotationColumn = column?.originType === "annotation_label";

  // Annotation cells store the typed envelope (e.g. {"selected":[...]},
  // {"value":"up"}, {"rating":4}). Pull out the primitive once so the rest
  // of the card (raw tab, copy, float-as-% formatting) sees the user-facing
  // value rather than the envelope JSON.
  const annotationUnwrapped = useMemo(() => {
    if (!isAnnotationColumn) return undefined;
    const parsed = parseAnnotationValue(value?.cellValue);
    if (Array.isArray(parsed)) return JSON.stringify(parsed);
    if (parsed && typeof parsed === "object") {
      if (Array.isArray(parsed.selected))
        return JSON.stringify(parsed.selected);
      if (typeof parsed.rating === "number") return String(parsed.rating);
      if (typeof parsed.value === "string" || typeof parsed.value === "number")
        return String(parsed.value);
      if (typeof parsed.text === "string") return parsed.text;
      return value?.cellValue;
    }
    return parsed == null ? "" : String(parsed);
  }, [isAnnotationColumn, value?.cellValue]);

  const formattedValue = useMemo(() => {
    if (isAnnotationColumn) return annotationUnwrapped;
    if (dataType === "float") {
      return `${getScorePercentage(parseFloat(value?.cellValue) * 10)}%`;
    }
    if (dataType === "datetime") {
      const date = new Date(value?.cellValue);
      return isNaN(date.getTime()) ? value?.cellValue : date.toLocaleString();
    }
    return value?.cellValue;
  }, [value?.cellValue, dataType, isAnnotationColumn, annotationUnwrapped]);

  const annotationContent = useMemo(
    () => (isAnnotationColumn ? renderAnnotationValue(value?.cellValue, theme) : null),
    [isAnnotationColumn, value?.cellValue, theme],
  );
  const parsedJson = useMemo(() => {
    if (!isJsonValue(formattedValue)) return null;
    try {
      return typeof formattedValue === "string"
        ? JSON.parse(formattedValue)
        : formattedValue;
    } catch {
      return null;
    }
  }, [formattedValue]);
  const formatFloatCellDiffvalue = useMemo(() => {
    if (dataType === "float" && value?.cellDiffValue?.length > 0) {
      return value?.cellDiffValue?.map((val) => {
        return {
          ...val,
          text: `${getScorePercentage(parseFloat(val?.text) * 10)}%`,
        };
      });
    }
    return value?.cellDiffValue;
  }, [value?.cellDiffValue, dataType]);

  return (
    <Accordion
      sx={{
        border: "1px solid",
        borderColor: isAgentsFinalNode ? "blue.500" : "divider",
      }}
      defaultExpanded
      disableGutters
    >
      <AccordionSummary
        sx={{
          flexDirection: "row",
          paddingLeft: 1,
          paddingRight: 2,
          backgroundColor: isAgentsFinalNode ? "blue.o10" : "background.paper",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          {renderIcon()}
          <CustomTooltip
            title={column?.headerName}
            show={column?.headerName?.length > 40}
            type="light"
          >
            <Typography
              typography="s1"
              color="text.secondary"
              fontWeight="fontWeightMedium"
            >
              {column?.headerName?.length > 40
                ? `${column?.headerName?.substring(0, 40)}...`
                : column?.headerName}
            </Typography>
          </CustomTooltip>
          <ShowComponent condition={isAgentsFinalNode}>
            <Chip
              label="Final"
              size="small"
              sx={{
                backgroundColor: "success.lighter",
                color: "success.main",
                borderRadius: 0.25,
                fontWeight: 600,
                fontSize: "11px",
                ":hover": {
                  backgroundColor: "success.lighter",
                  color: "success.main",
                },
              }}
            />
          </ShowComponent>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ padding: 0 }}>
        <Box
          sx={{
            paddingX: 2,
            paddingBottom: 1,
            backgroundColor: isAgentsFinalNode
              ? "blue.o10"
              : "background.paper",
          }}
        >
          {thoughts && (
            <Accordion
              defaultExpanded
              disableGutters
              sx={{
                mb: 1,
                border: "1px solid",
                borderColor: "action.selected",
              }}
            >
              <AccordionSummary
                sx={{
                  flexDirection: "row",
                  paddingLeft: 1,
                  paddingRight: 2,
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <SvgColor
                    src={"/assets/icons/ic_thoughts.svg"}
                    sx={{
                      height: "20px",
                      width: "20px",
                      bgcolor: "primary.main",
                    }}
                  />
                  <Typography
                    typography="s1"
                    color="text.secondary"
                    fontWeight="fontWeightMedium"
                  >
                    Thoughts
                  </Typography>
                </Box>
              </AccordionSummary>
              <AccordionDetails sx={{ paddingX: 1.5 }}>
                <Box
                  sx={{
                    padding: (theme) => theme.spacing(0.5, 0.75),
                    overflowWrap: "break-word",
                    maxHeight: "200px",
                    overflowY: "auto",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 0.5,
                  }}
                >
                  <Typography
                    typography={"s2_1"}
                    sx={{ color: "text.primary" }}
                  >
                    {thoughts.join("\n\n")}
                  </Typography>
                </Box>
              </AccordionDetails>
            </Accordion>
          )}
          <Box
            sx={{
              border: "1px solid",
              borderColor: "action.selected",
              borderRadius: "8px",
              mb: 1,
            }}
          >
            <Box
              sx={
                shouldApplyDefaultSx
                  ? { borderBottom: 1, borderColor: "common.white" }
                  : undefined
              }
            >
              {showTabs ? (
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "8px 12px",
                  }}
                >
                  <Tabs
                    textColor="primary"
                    value={tabValue}
                    onChange={handleTabChange}
                    TabIndicatorProps={{ style: { display: "none" } }}
                    sx={{
                      minHeight: 32,
                      "& .MuiTab-root": {
                        minHeight: 32,
                        paddingX: "8px",
                        paddingY: "4px",
                        fontWeight: theme.typography["fontWeightMedium"],
                        typography: "s1",
                        "&:not(:last-of-type)": {
                          marginRight: "0px !important",
                        },
                        "&:not(.Mui-selected)": {
                          color: theme.palette.text.secondary,
                        },
                        "&.Mui-selected": { color: theme.palette.primary.main },
                      },
                      "& .MuiTabs-flexContainer": {
                        gap: "20px",
                        display: "flex",
                      },
                    }}
                  >
                    <Tab value="markdown" label="Markdown" />
                    <Tab value="raw" label="Raw" />
                    {showDiff && <Tab value="difference" label="Difference" />}
                  </Tabs>
                  {allowCopy && (
                    <SvgColor
                      src="/assets/icons/ic_copy.svg"
                      alt="Copy"
                      sx={{
                        width: 20,
                        height: 20,
                        color: "text.disabled",
                        cursor: "pointer",
                      }}
                      onClick={() => {
                        copyToClipboard(value?.cellValue);
                        enqueueSnackbar("Copied to clipboard", {
                          variant: "success",
                        });
                      }}
                    />
                  )}
                </Box>
              ) : null}
              {showTabs ? (
                <>
                  <ShowComponent condition={tabValue === "raw"}>
                    <Box
                      sx={{
                        paddingX: "16px",
                        paddingY: "12px",
                        backgroundColor: "background.neutral",
                        overflowWrap: "break-word",
                        overflowX: "auto",
                        maxHeight: "155px",
                        ...sx,
                      }}
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          wordBreak: "break-all",
                          color: isEmptyField && "text.disabled",
                          fontSize: isEmptyField && "13px",
                        }}
                      >
                        {["array", "json"].includes(dataType) &&
                        isJsonValue(formattedValue) ? (
                          <CustomJsonViewer object={parsedJson} />
                        ) : Array.isArray(formattedValue) ? (
                          <GenerateDiffText cellText={formattedValue} />
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
                        wordBreak: "break-word",
                        backgroundColor: "background.neutral",
                        ...sx,
                      }}
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          color: isEmptyField && "text.disabled",
                          fontSize: isEmptyField && "13px",
                        }}
                      >
                        <ShowComponent
                          condition={
                            isAnnotationColumn && annotationContent !== null
                          }
                        >
                          {annotationContent}
                        </ShowComponent>
                        <ShowComponent
                          condition={
                            !isAnnotationColumn &&
                            column?.originType === "run_prompt" &&
                            isJsonValue(formattedValue) &&
                            parsedJson !== null
                          }
                        >
                          <CustomJsonViewer
                            object={parsedJson}
                            clickToExpandNode
                          />
                        </ShowComponent>
                        <ShowComponent
                          condition={
                            !isAnnotationColumn &&
                            !(
                              column?.originType === "run_prompt" &&
                              isJsonValue(formattedValue) &&
                              parsedJson !== null
                            )
                          }
                        >
                          <CellMarkdown spacing={0} text={formattedValue} />
                        </ShowComponent>
                      </Typography>
                    </Box>
                  </ShowComponent>
                  <ShowComponent
                    condition={showDiff && tabValue === "difference"}
                  >
                    <Box
                      sx={{
                        paddingX: "16px",
                        paddingY: "12px",
                        overflowWrap: "break-word",
                        backgroundColor: "background.neutral",
                        "& > p, span": {
                          typography: "s1",
                          fontWeight: "fontWeightRegular",
                        },
                      }}
                    >
                      <GenerateDiffText cellText={formatFloatCellDiffvalue} />
                    </Box>
                  </ShowComponent>
                </>
              ) : (
                <Box
                  sx={{
                    paddingX: "16px",
                    paddingY: "12px",
                    backgroundColor: "background.neutral",
                    overflowWrap: "break-word",
                    overflowX: "auto",
                    maxHeight: "155px",
                    ...sx,
                  }}
                >
                  <Typography
                    variant="body2"
                    sx={{
                      wordBreak: "break-all",
                      color: isEmptyField && "text.disabled",
                      fontSize: isEmptyField && "13px",
                    }}
                  >
                    {["array", "json"].includes(dataType) &&
                    isJsonValue(formattedValue) ? (
                      <CustomJsonViewer object={parsedJson} />
                    ) : Array.isArray(formattedValue) ? (
                      <GenerateDiffText cellText={formattedValue} />
                    ) : (
                      formattedValue
                    )}
                  </Typography>
                </Box>
              )}
            </Box>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};
DatapointCard.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
  allowCopy: PropTypes.bool,
  onDiffClick: PropTypes.func,
  showDiff: PropTypes.bool,
  activeTab: PropTypes.bool,
  sx: PropTypes.object,
  isEmptyField: PropTypes.bool,
  indColsDifTracker: PropTypes.object,
  showTabs: PropTypes.bool,
  isAgentsFinalNode: PropTypes.bool,
};
export default DatapointCard;
