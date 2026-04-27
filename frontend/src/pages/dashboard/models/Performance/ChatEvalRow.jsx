import { TableCell, TableRow, useTheme } from "@mui/material";
import React from "react";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import PerformanceTableInfo from "./PerformanceTableInfo";
import PerformanceTableCell from "./PerformanceTableCell";
import { format } from "date-fns";
import PropTypes from "prop-types";
import ModelInputOutputCell from "src/sections/model/ModelInputOutputCell";

const ChatEvalRow = ({
  row,
  isProcessing,
  setPerformanceDetail,
  setSelectedTags,
  setSelectedImages,
}) => {
  const theme = useTheme();

  const onImageClick = (curUrl) => {
    const images = [];
    let defaultIdx = 0;
    row.modelInput
      ?.filter((o) => o["url"] !== undefined)
      ?.forEach((url) => {
        if (curUrl === url.url) defaultIdx = images.length;
        images.push({ src: url.url });
      });
    row.modelOutput
      ?.filter((o) => o["url"] !== undefined)
      ?.forEach((url) => {
        if (curUrl === url.url) defaultIdx = images.length;
        images.push({ src: url.url });
      });
    setSelectedImages({ images, defaultIdx });
  };

  return (
    <TableRow
      hover
      sx={{
        "&:hover": {
          cursor: "pointer",
          backgroundColor: `${theme.palette.primary.main}`,
        },
      }}
      onClick={() => {
        if (isProcessing) return;
        setPerformanceDetail(row);
      }}
    >
      <TableCell
        sx={{
          padding: 0,
          position: "relative",
          width: "10px",
        }}
      >
        <div
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            width: "6px",
            display: "inline-block",
            backgroundColor: isProcessing
              ? "white"
              : interpolateColorBasedOnScore(row.score),
          }}
        />
      </TableCell>
      <ModelInputOutputCell
        content={row.modelInput}
        contentType={row.modelInputType}
        onImageClick={onImageClick}
      />

      <ModelInputOutputCell
        content={row.modelOutput}
        contentType={row.modelOutputType}
        onImageClick={onImageClick}
      />

      <PerformanceTableInfo row={row} setSelectedTags={setSelectedTags} />
      <PerformanceTableCell sx={{ minWidth: 60 }}>
        {format(new Date(row.date), "dd/MM/yyyy")}
      </PerformanceTableCell>
    </TableRow>
  );
};

ChatEvalRow.propTypes = {
  row: PropTypes.object,
  isProcessing: PropTypes.bool,
  setPerformanceDetail: PropTypes.func,
  setSelectedTags: PropTypes.func,
  setSelectedImages: PropTypes.func,
};

export default ChatEvalRow;
