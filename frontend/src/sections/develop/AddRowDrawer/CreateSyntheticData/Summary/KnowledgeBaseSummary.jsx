import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const KnowledgeBaseSummary = ({ showTitle }) => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        backgroundColor: "status.warning.bg",
        border: "1px solid",
        borderColor: "status.warning.border",
        padding: "12px",
        borderRadius: "8px",
      }}
    >
      {showTitle && (
        <Typography
          variant="s1"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
        >
          Why Knowledge Base is important?
        </Typography>
      )}
      <Box>
        {objectiveSummary.map((item, index) => (
          <Box key={index} sx={{ display: "flex" }}>
            <Typography
              variant="s2"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              {index + 1}.&nbsp;
            </Typography>
            <Typography
              variant="s2"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              {item}
            </Typography>
          </Box>
        ))}
      </Box>
    </Box>
  );
};

export default KnowledgeBaseSummary;

KnowledgeBaseSummary.propTypes = {
  showTitle: PropTypes.bool,
};

const objectiveSummary = [
  "The Knowledge Base (KB) acts as the foundation for grounded and context-aware synthetic data generation. All outputs are informed by the content you upload, which is semantically processed and abstracted to guide generation. This ensures the synthetic data remains factually consistent, domain-aligned, and contextually relevant, while allowing for controlled diversity and variation in form.",
  "This eliminates hallucinations and allows the system to reflect your organization’s language, structure, and use cases. By anchoring generation to your KB, we ensure relevance, reliability, and control—even at scale.",
];
