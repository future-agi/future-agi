import { Box, Button, Chip, Typography, useTheme } from "@mui/material";
import React from "react";
import HelperText from "../HelperText";
import PropTypes from "prop-types";
import { getScorePercentage } from "src/utils/utils";
import CellMarkdown from "src/sections/common/CellMarkdown";

const EvaluationTest = ({ testingEvalData, onClose }) => {
  const theme = useTheme();

  const renderResponse = (data) => {
    if (data.outputType === "Pass/Fail") {
      return <CellMarkdown spacing={0} text={data?.reason} />;
    }

    if (data.outputType === "score") {
      return <CellMarkdown spacing={0} text={data?.reason} />;
    }

    if (data.outputType === "choices") {
      return (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
          {!data?.output?.length ? "No Response" : <></>}
          {data?.output?.map((o) => (
            <Chip
              label={o}
              key={o}
              variant="outlined"
              size="small"
              color="primary"
            />
          ))}
        </Box>
      );
    }

    if (data.outputType === "reason") {
      return (
        <Box sx={{ wordWrap: "break-word" }}>
          <CellMarkdown spacing={0} text={data?.reason} />
        </Box>
      );
    }

    return "No Response";
  };

  const renderSideBar = (data) => {
    if (data.outputType === "Pass/Fail") {
      return data?.output;
    }

    if (data.outputType === "score") {
      return `${getScorePercentage(data?.output * 10)}%`;
    }
  };

  return (
    <Box
      sx={{
        width: "30vw",
        height: "100vh",
        borderRight: "1px solid",
        borderColor: "divider",
        padding: "20px",
        display: "flex",
        flexDirection: "column",
        gap: "20px",
        overflowY: "auto",
      }}
    >
      <Box display="flex" flexDirection="column" gap={theme.spacing(1)}>
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography fontSize="13px" fontWeight={600} color="text.secondary">
            Evaluation Test
          </Typography>
          <Button variant="soft" size="small" onClick={onClose}>
            Close
          </Button>
        </Box>
        <HelperText text="Run a sample evals to see how it works" />
      </Box>
      <Box sx={{ overflow: "auto" }}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "20px",
            // overflowY: "auto",
            minWidth: "min-content",
            wordWrap: "break-word",
          }}
        >
          {testingEvalData?.data?.result?.responses?.map((d) => (
            <Box
              sx={{
                border: "1px solid",
                borderRadius: 1,
                borderColor: "divider",
                minWidth: "min-content",
                wordWrap: "break-word",
              }}
              key={d.response}
            >
              <Box
                sx={{
                  padding: 1,
                  borderBottom: "1px solid ",
                  borderBottomColor: "divider",
                  justifyContent: "space-between",
                  display: "flex",
                }}
              >
                <Typography variant="subtitle2" color="text.secondary">
                  Response
                </Typography>
                <Typography variant="subtitle2" color="text.secondary">
                  {renderSideBar(d)}
                </Typography>
              </Box>
              <Box sx={{ padding: 1 }}>
                <Typography variant="body2">{renderResponse(d)}</Typography>
              </Box>
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  );
};

EvaluationTest.propTypes = {
  testingEvalData: PropTypes.object,
  onClose: PropTypes.func,
};

export default EvaluationTest;
