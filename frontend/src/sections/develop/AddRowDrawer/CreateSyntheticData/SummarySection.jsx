import { Box, Button, Typography, useTheme } from "@mui/material";
import React, { useState } from "react";
import HelpCreateSyntheticData from "./HelpCreateSyntheticData";
import DescriptionSummary from "./Summary/DescriptionSummary";
import ObjectiveSummary from "./Summary/ObjectiveSummary";
import WrittingPatternSummary from "./Summary/WrittingPatternSummary";
import PropTypes from "prop-types";
import DetailFormSummary from "./Summary/DetailFormSummary";
import AddColumnFormSummary from "./Summary/AddColumnFormSummary";
import KnowledgeBaseSummary from "./Summary/KnowledgeBaseSummary";

const SummarySection = ({ focusField, activeTab, selectedKB }) => {
  const [open, setOpen] = useState(false);
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "16px",
        height: "100%",
        overflowY: "auto",
      }}
    >
      <HelpCreateSyntheticData open={open} onClose={() => setOpen(false)} />
      {activeTab.value === "addDetails" && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <Typography
            variant="s1"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            How to create synthetic data?
          </Typography>
          <Box
            sx={{
              padding: "12px",
              display: "flex",
              gap: "12px",
              backgroundColor: "status.info.bg",
              border: "1px solid",
              borderColor: "status.info.border",
              borderRadius: "4px",
            }}
          >
            <Box
              sx={{
                width: "160px",
                height: "90px",
                border: "1px solid",
                borderRadius: "4px",
              }}
            >
              <img
                src={
                  isDark
                    ? "/assets/synthetic-data-thumbnail_dark.png"
                    : "/assets/synthetic-data-thumbnail.png"
                }
                width={"158px"}
                height={"88px"}
                style={{
                  borderRadius: "4px",
                  cursor: "pointer",
                }}
                onClick={() => setOpen(true)}
              />
            </Box>
            <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <Typography
                variant="s1"
                fontWeight={"fontWeightMedium"}
                color="text.primary"
              >
                How to create synthetic dataset?
              </Typography>
              <Typography
                variant="s2"
                fontWeight={"fontWeightRegular"}
                color="text.secondary"
              >
                Create data set with three simple steps!
              </Typography>
              <Button
                variant="outlined"
                size="small"
                sx={{
                  marginTop: "12px",
                  borderColor: "primary.main",
                  width: "max-content",
                  color: "primary.main",
                }}
                onClick={() => setOpen(true)}
              >
                Play Video
              </Button>
            </Box>
          </Box>
        </Box>
      )}
      {/* {focusField === "name" && <SyntheticDataSummary showTitle={true} />} */}
      {focusField === "kb_id" && <KnowledgeBaseSummary showTitle={true} />}
      {focusField === "description" && <DescriptionSummary showTitle={true} />}
      {focusField === "useCase" && <ObjectiveSummary showTitle={true} />}
      {focusField === "pattern" && <WrittingPatternSummary showTitle={true} />}
      {activeTab.value !== "addDetails" && (
        <DetailFormSummary selectedKB={selectedKB} />
      )}
      {activeTab.value === "addDescription" && <AddColumnFormSummary />}
    </Box>
  );
};

export default SummarySection;

SummarySection.propTypes = {
  focusField: PropTypes.string,
  activeTab: PropTypes.object,
  selectedKB: PropTypes.string,
};
