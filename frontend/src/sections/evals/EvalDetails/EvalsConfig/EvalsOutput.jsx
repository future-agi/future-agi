import { Box, Chip, Divider, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CellMarkdown from "src/sections/common/CellMarkdown";
import {
  getLabel,
  getStatusColor,
} from "src/sections/develop-detail/DataTab/common";
import CompositeResultView from "src/sections/evals/components/CompositeResultView";

const EvalsOutput = ({ results }) => {
  const theme = useTheme();
  const compositeResult = results?.compositeResult;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: (theme) => theme.spacing(1),
        overflow: "auto",
        minHeight: "230px",
        height: "100%",
        position: "relative",
      }}
    >
      <Box
        sx={{
          position: "sticky",
          top: 0,
          width: "100%",
          zIndex: 1,
          backgroundColor: "background.paper",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: 1,
          }}
        >
          <Typography
            typography="s1"
            fontWeight={"fontWeightRegular"}
            color="text.primary"
          >
            Result
          </Typography>
          {!compositeResult && (results?.output || results?.output == 0) && (
            <Chip
              size="small"
              label={getLabel(results?.output)}
              sx={{
                padding: "4px 8px",
                ...getStatusColor(results?.output, theme),
                transition: "none",
                "&:hover": {
                  backgroundColor: getStatusColor(results?.output, theme)
                    ?.backgroundColor, // Lock it to same color
                  boxShadow: "none",
                },
              }}
            />
          )}
        </Box>
        <Divider orientation="horizontal" />
      </Box>

      {compositeResult ? (
        <CompositeResultView compositeResult={compositeResult} />
      ) : (
        <Box
          sx={{
            minHeight: "calc(100% - 48px)",
            padding: 1,
            color: "text.primary",
            "& div, pre": {
              whiteSpace: "pre-wrap",
            },
          }}
        >
          {typeof results?.reason === "string" ? (
            <CellMarkdown fontSize={12} text={results.reason} />
          ) : (
            results?.reason?.map((item, index) => (
              <CellMarkdown key={index} fontSize={12} text={item} />
            ))
          )}
        </Box>
      )}
    </Box>
  );
};

export default EvalsOutput;

EvalsOutput.propTypes = {
  children: PropTypes.node,
  results: PropTypes.object,
};
