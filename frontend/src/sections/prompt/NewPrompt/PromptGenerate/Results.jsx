import React, { Fragment, useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Tab, Tabs, Typography } from "@mui/material";
import ResultData from "./ResultData";
import ResultSummary from "./ResultSummary";
import CellMarkdown from "src/sections/common/CellMarkdown";

const RESULT_TEXT = [
  "- Write a prompt in the left column and click save and run to see response",
  "- Editing prompts, or changing model options will create a new version",
  "- Write variables like this {{VARIABLE_NAME}}",
  "- Add message to simulate a conversation",
];

const Results = ({ resultState, output }) => {
  // Initialize state with the output data
  const [, setColumns] = useState([]);
  const [, setRows] = useState([]);

  // Only fetch when resultState changes and is truthy
  // Handle data updates in a separate effect
  useEffect(() => {
    if (output?.output?.length > 0) {
      setColumns(
        output?.output.map((result, index) => ({
          field: `Result ${index + 1}`,
          flex: 1,
          cellRenderer: (p) => (
            <Box overflow={"auto"} height={"100%"}>
              <CellMarkdown spacing={0} text={p.value} />
            </Box>
          ),
          minWidth: 300,
          autoHeight: true,
          wrapText: true,
        })),
      );
      setRows([
        output?.output?.reduce((acc, result, index) => {
          acc[`Result ${index + 1}`] = result;
          return acc;
        }, {}),
      ]);
    }
  }, [output]);
  const [selectedTab, setSelectedTab] = useState("results");

  const handleChange = (event, newValue) => {
    setSelectedTab(newValue);
  };

  const isEvalPreset = Object.keys(output?.evaluationResults || {})?.length > 0;

  const renderHeader = () => {
    if (isEvalPreset) {
      return (
        <Box sx={{ borderBottom: "1px solid", color: "divider" }}>
          <Tabs
            value={selectedTab}
            onChange={handleChange}
            aria-label="basic tabs example"
            textColor="primary"
          >
            <Tab label="Results" value="results" />
            <Tab label="Summary" value="summary" />
          </Tabs>
        </Box>
      );
    }

    return (
      <Typography
        variant="subheader"
        color="text.disabled"
        // sx={{ margin: "17px 0 0" }}
      >
        RESULTS
      </Typography>
    );
  };

  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        padding: "17px 17px 10px",
        overflow: "auto",
        gap: "30px",
        height: "100%",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "19px",
          height: "100%",
          width: "100%",
          flex: 1,
          overflow: "auto",
        }}
      >
        {renderHeader()}
        {resultState === "Completed" &&
        (output?.output?.length > 0 || isEvalPreset) ? (
          selectedTab === "results" ? (
            <ResultData data={output} />
          ) : (
            <ResultSummary data={output} />
          )
        ) : (
          <Box
            sx={{
              border: "2px solid var(--border-light)",
              borderRadius: "8px",
              padding: "16px 16px 16px 24px",
              flex: "1",
              overflow: "auto",
            }}
          >
            {!resultState && (
              <Fragment>
                <Typography
                  color="text.disabled"
                  fontWeight={600}
                  fontSize={12}
                  margin={"10px 0"}
                >
                  Introduction
                </Typography>
                <Box sx={{ padding: "0 0 0 10px" }}>
                  {RESULT_TEXT.map((text) => (
                    <Typography
                      key={text}
                      variant="subtitle2"
                      color="text.primary"
                      fontWeight={"fontWeightRegular"}
                    >
                      {text}
                    </Typography>
                  ))}
                </Box>
              </Fragment>
            )}
            {resultState === "Completed" ? (
              <CellMarkdown spacing={0} text={output?.output?.[0]} />
            ) : (
              <Typography
                variant="subtitle2"
                color={
                  resultState === "Completed"
                    ? "text.primary"
                    : "text.secondary"
                }
                fontWeight={"fontWeightRegular"}
              >
                {resultState === "Running" ? "Generating..." : resultState}
              </Typography>
            )}
          </Box>
        )}
      </Box>
      {/* {hideInitialText && (
        <Button variant="contained" color="primary" fullWidth>
          Continue
        </Button>
      )} */}
    </Box>
  );
};

Results.propTypes = {
  resultState: PropTypes.any,
  output: PropTypes.object,
};

Results.defaultProps = {
  output: {},
};

export default Results;
