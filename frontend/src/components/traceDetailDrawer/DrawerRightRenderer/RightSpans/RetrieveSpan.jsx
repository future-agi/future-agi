import React, { useMemo, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../SpanAccordianElements";
import { Box, IconButton, Tab, Tabs, Typography, alpha } from "@mui/material";
import { copyToClipboard, getScorePercentage } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { useNavigate } from "react-router";

const RetreiveSpan = ({
  value,
  column,
  allowCopy = false,
  showScore = false,
  retreiveDocs = {},
  promptName,
  promptTemplateId,
}) => {
  const [tabValues, setTabValues] = useState({});
  const navigate = useNavigate();
  const dataType = column?.dataType;

  const docsArray = Object.entries(retreiveDocs).map(([_, doc]) => ({
    id: doc?.id,
    value: doc?.value,
    score: doc?.score,
  }));

  const handleTabChange = (docId) => (event, newValue) => {
    setTabValues((prev) => ({ ...prev, [docId]: newValue }));
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
          <Box>
            {column?.headerName}{" "}
            {docsArray.length > 0 ? `(${docsArray.length})` : ""}
          </Box>
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
        <Box sx={{ paddingX: 2, paddingBottom: 1 }}>
          {(docsArray.length > 0
            ? docsArray
            : [{ id: "default", value: formattedValue }]
          ).map((doc, index) => {
            const docId = doc.id || `doc-${index}`;
            return (
              <Box
                key={docId}
                sx={{
                  border: "1px solid",
                  borderColor: "action.selected",
                  borderRadius: "8px",
                  marginBottom: 2,
                }}
              >
                <Box
                  sx={{
                    borderBottom: 1,
                    borderColor: "divider",
                    position: "relative",
                  }}
                >
                  {doc.value && doc.id !== "default" && (
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        height: "40px",
                        paddingX: 1.5,
                        borderBottom: "1px solid",
                        borderColor: "divider",
                        justifyContent: "space-between",
                        position: "relative",
                      }}
                    >
                      <Typography
                        sx={{
                          color: "text.secondary",
                          fontSize: "13px",
                          fontWeight: "500",
                        }}
                      >
                        Document {doc.id}
                      </Typography>
                      {showScore && doc.score !== undefined && (
                        <Box
                          sx={{
                            padding: 0.5,
                            paddingX: 1.5,
                            borderRadius: 1,
                            backgroundColor: (theme) =>
                              alpha(theme.palette.error.main, 0.08),
                            color: "error.dark",
                            border: "1px solid",
                            borderColor: (theme) =>
                              alpha(theme.palette.error.main, 0.08),
                            whiteSpace: "nowrap",
                            marginRight: 5,
                          }}
                        >
                          <Typography
                            variant="body2"
                            sx={{ fontSize: "12px", fontWeight: 500 }}
                          >
                            Score - {doc.score}
                          </Typography>
                        </Box>
                      )}
                    </Box>
                  )}
                  <Box
                    sx={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <Tabs
                      textColor="primary"
                      value={tabValues[docId] || "markdown"}
                      onChange={handleTabChange(docId)}
                      TabIndicatorProps={{
                        style: { display: "none" },
                      }}
                      sx={{
                        minHeight: 32,
                        "& .MuiTab-root": {
                          minHeight: 32,
                          padding: "12px 12px",
                          marginRight: 0,
                        },
                      }}
                    >
                      <Tab value="markdown" label="Markdown" />
                      <Tab value="raw" label="Raw" />
                    </Tabs>
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
                            copyToClipboard(doc.value);
                            enqueueSnackbar("Copied to clipboard", {
                              variant: "success",
                            });
                          }}
                        >
                          <Iconify
                            icon="basil:copy-outline"
                            sx={{ color: "text.secondary" }}
                          />
                        </IconButton>
                      </Box>
                    )}
                  </Box>

                  <ShowComponent
                    condition={(tabValues[docId] || "markdown") === "raw"}
                  >
                    <Box
                      sx={{
                        paddingX: "16px",
                        paddingY: "12px",
                        borderTop: "1px solid",
                        borderColor: "divider",
                        overflowWrap: "break-word",
                      }}
                    >
                      <ShowComponent condition={doc?.value}>
                        <CustomJsonViewer object={doc?.value} />
                      </ShowComponent>
                    </Box>
                  </ShowComponent>
                  <ShowComponent
                    condition={(tabValues[docId] || "markdown") === "markdown"}
                  >
                    <Box
                      sx={{
                        paddingX: "16px",
                        paddingY: "12px",
                        overflowWrap: "break-word",
                        borderTop: "1px solid",
                        borderColor: "divider",
                      }}
                    >
                      <Typography variant="body2">
                        <CellMarkdown
                          spacing={0}
                          text={JSON.stringify(doc?.value)}
                        />
                      </Typography>
                    </Box>
                  </ShowComponent>
                </Box>
              </Box>
            );
          })}
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

RetreiveSpan.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
  allowCopy: PropTypes.bool,
  showScore: PropTypes.bool,
  retreiveDocs: PropTypes.object,
  promptName: PropTypes.string,
  promptTemplateId: PropTypes.string,
};

export default RetreiveSpan;
