import React from "react";
import PropType from "prop-types";
import PerformanceTableCell from "./PerformanceTableCell";
import { Box, Chip } from "@mui/material";
import {
  getPerformanceTagColor,
  getScorePercentage,
  getTabLabel,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import CircularProgressWithLabel from "src/components/circular-progress-with-label/CircularProgressWithLabel";
import TruncatedText from "src/components/truncate-string/TruncateString";

const PerformanceTableInfo = ({ row, setSelectedTags }) => {
  const isProcessing = !row?.score;

  if (isProcessing) {
    return (
      <PerformanceTableCell colSpan={3}>
        <Box sx={{ display: "flex", justifyContent: "center" }}>
          <Chip label="Processing" color="success" size="small" />
        </Box>
      </PerformanceTableCell>
    );
  }

  return (
    <>
      <PerformanceTableCell>
        <CircularProgressWithLabel
          color={interpolateColorBasedOnScore(row.score)}
          value={getScorePercentage(row.score)}
        />
      </PerformanceTableCell>
      <PerformanceTableCell sx={{ minWidth: "250px" }}>
        <TruncatedText maxLines={3}>{row.explanation}</TruncatedText>
      </PerformanceTableCell>
      <PerformanceTableCell>
        <Box
          sx={{
            display: "flex",
            gap: 1,
            flexWrap: "wrap",
            height: "100%",
            width: "500px",
          }}
        >
          {row?.tags?.map((tag) => {
            const color = getPerformanceTagColor(tag);
            return (
              <Chip
                variant="soft"
                color={color}
                key={tag}
                label={getTabLabel(tag)}
                clickable
                size="small"
                sx={{
                  fontSize: "11px",
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  setSelectedTags((existingArr) => {
                    if (existingArr.includes(tag)) {
                      return existingArr.filter((t) => t !== tag);
                    } else {
                      return [...existingArr, tag];
                    }
                  });
                }}
              />
            );
          })}
        </Box>
      </PerformanceTableCell>
    </>
  );
};

PerformanceTableInfo.propTypes = {
  row: PropType.object,
  setSelectedTags: PropType.func,
};

export default PerformanceTableInfo;
