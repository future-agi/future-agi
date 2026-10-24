import React, { useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../SpanAccordianElements";
import { Box, Stack, Tab, Tabs, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import CustomDataPointCard from "../CustomDataPointCard";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import Image from "src/components/image";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip";
import { useNavigate } from "react-router";
import { isJson } from "../getSpanData";

function CustomTabPanel(props) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`simple-tabpanel-${index}`}
      aria-labelledby={`simple-tab-${index}`}
      {...other}
    >
      {value === index && <Box>{children}</Box>}
    </div>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  index: PropTypes.number.isRequired,
  value: PropTypes.number.isRequired,
};

const LLMSpan = ({
  column,
  tabData,
  model = null,
  modelLogo,
  promptName,
  promptTemplateId,
}) => {
  const theme = useTheme();
  const [tabIndex, setTabIndex] = useState(0);
  const navigate = useNavigate();
  const tabKeys = Object.keys(tabData).filter((k) => {
    const value = tabData?.[k];
    if (k === "invocationParams")
      return !!value && Object.keys(value || {}).length > 0;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "string") return value.trim() !== "";
    if (typeof value === "object")
      return value !== null && Object.keys(value).length > 0;
    return !!value;
  });

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
            justifyContent: "space-between",
            paddingX: (theme) => theme.spacing(1),
            alignItems: "center",
            width: "100%",
          }}
        >
          <span>{column?.headerName}</span>
          <ShowComponent condition={model}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                padding: (theme) => theme.spacing(0.5),
                paddingX: (theme) => theme.spacing(1.5),
                borderRadius: (theme) => theme.spacing(1),
                color: "text.secondary",
                border: "1px solid",
                borderColor: "text.disabled",
                whiteSpace: "nowrap",
                gap: (theme) => theme.spacing(1),
              }}
            >
              <ShowComponent condition={modelLogo}>
                <Image
                  src={modelLogo}
                  alt="Model Logo"
                  ratio="1/1"
                  flexShrink={0}
                  sx={{
                    width: "15px",
                    height: "15px",
                  }}
                />
              </ShowComponent>
              <CustomTooltip arrow show={model?.length > 30} title={model}>
                <Typography
                  typography="s2"
                  color="text.primary"
                  sx={{
                    maxWidth: "300px",
                    textOverflow: "ellipsis",
                    overflow: "hidden",
                  }}
                >
                  {model}
                </Typography>
              </CustomTooltip>
            </Box>
          </ShowComponent>
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
      <AccordionDetails sx={{ padding: 0 }}>
        <Box>
          <Box
            sx={{
              borderBottom: 1,
              borderColor: "divider",
              width: "100%",
            }}
          >
            <Tabs
              value={tabIndex}
              onChange={(_, newValue) => setTabIndex(newValue)}
              aria-label="llm tabs"
              textColor="primary"
              indicatorColor="primary"
              sx={{
                borderBottom: (theme) => theme.spacing(1),
                borderColor: "divider",
                "& .MuiTab-root:not(:last-of-type)": {
                  marginRight: (theme) => theme.spacing(2),
                  marginTop: (theme) => theme.spacing(0),
                },
              }}
            >
              {tabKeys.map((key) => (
                <Tab
                  key={key}
                  label={key
                    .replace(/([A-Z])/g, " $1")
                    .replace(/^./, (str) => str.toUpperCase())}
                  sx={{
                    marginLeft: (theme) => theme.spacing(3),
                  }}
                />
              ))}
            </Tabs>
          </Box>
          {tabKeys.map((key, index) => (
            <CustomTabPanel key={key} value={tabIndex} index={index}>
              {key === "invocationParams" ? (
                <Box
                  sx={{
                    typography: "s1",
                    whiteSpace: "pre-wrap",
                    paddingX: (theme) => theme.spacing(1.5),
                    paddingY: (theme) => theme.spacing(1),
                    overflow: "auto",
                  }}
                >
                  <CustomJsonViewer object={tabData[key]} />
                </Box>
              ) : key === "input" ? (
                <Stack padding={2} spacing={2}>
                  {Array.isArray(tabData[key]) ? (
                    tabData[key]?.map((obj, index) => (
                      <Stack
                        key={index}
                        border={"1px solid var(--border-default)"}
                        borderRadius="4px"
                        padding={1}
                      >
                        {" "}
                        <CustomJsonViewer object={obj} />
                      </Stack>
                    ))
                  ) : (
                    <Stack
                      key={index}
                      border={"1px solid var(--border-default)"}
                      borderRadius="4px"
                      padding={1}
                    >
                      <CustomJsonViewer object={tabData[key]} />
                    </Stack>
                  )}
                </Stack>
              ) : (
                <Box sx={{ padding: 1 }}>
                  {["object", "boolean", "number"].includes(
                    typeof tabData?.[key],
                  ) ? (
                    <Box sx={{ padding: (theme) => theme.spacing(2) }}>
                      {Array.isArray(tabData[key]) ? (
                        <Stack spacing={2}>
                          {" "}
                          {tabData[key]?.map(({ role, content }) => (
                            <CustomDataPointCard
                              key={role}
                              value={{ cellValue: content }}
                              label={
                                role?.charAt(0).toUpperCase() + role?.slice(1)
                              }
                              allowCopy
                            />
                          ))}
                        </Stack>
                      ) : (
                        <CustomJsonViewer
                          object={
                            isJson(tabData[key])
                              ? JSON.parse(tabData[key])
                              : tabData[key]
                          }
                        />
                      )}
                    </Box>
                  ) : (
                    <CustomDataPointCard
                      value={{ cellValue: tabData[key] }}
                      label={
                        tabData.input !== undefined && tabData.input !== null
                          ? "User"
                          : tabData.output !== undefined &&
                              tabData.output !== null
                            ? "System"
                            : ""
                      }
                      allowCopy
                    />
                  )}
                </Box>
              )}
            </CustomTabPanel>
          ))}
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

LLMSpan.propTypes = {
  column: PropTypes.object,
  tabData: PropTypes.object,
  model: PropTypes.string,
  modelLogo: PropTypes.string,
  promptName: PropTypes.string,
  promptTemplateId: PropTypes.string,
};

export default LLMSpan;
