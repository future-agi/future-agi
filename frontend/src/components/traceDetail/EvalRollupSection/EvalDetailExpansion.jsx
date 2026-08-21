import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Markdown from "react-markdown";
import Iconify from "src/components/iconify";
import EvalErrorLocalization from "../EvalErrorLocalization";
import { spanPassed } from "./utils";

// Explanation + error localizer (fetched on demand) + Fix with Falcon.
const EvalDetailExpansion = ({
  span,
  evalConfigId,
  evalName,
  outputType,
  onFixWithFalcon,
  pl = 4.5,
}) => {
  const explanation = span.explanation;
  const hasLoc = !!(span.span_id && evalConfigId);

  return (
    <Box
      sx={{
        pl,
        pr: 1.5,
        py: 0.75,
        bgcolor: "background.default",
        borderBottom: "1px solid",
        borderColor: "divider",
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      {explanation && (
        <Box
          sx={{
            fontSize: 11,
            color: "text.secondary",
            lineHeight: 1.6,
            "& p": { m: 0, mb: 0.5 },
          }}
        >
          <Markdown>{explanation}</Markdown>
        </Box>
      )}
      {hasLoc && (
        <EvalErrorLocalization
          observationSpanId={span.span_id}
          customEvalConfigId={evalConfigId}
          initialAnalysis={null}
          initialStatus={null}
        />
      )}
      {!spanPassed(span, outputType) && onFixWithFalcon && (
        <Box
          onClick={(e) => {
            e.stopPropagation();
            onFixWithFalcon({
              level: "eval",
              ev: {
                eval_config_id: evalConfigId,
                eval_name: evalName,
                span_id: span.span_id,
                span_name: span.span_name,
                score: typeof span.value === "number" ? span.value : undefined,
                explanation: span.explanation,
              },
            });
          }}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            px: 0.75,
            py: 0.25,
            alignSelf: "flex-start",
            border: "1px solid",
            borderColor: (t) => alpha(t.palette.primary.main, 0.4),
            borderRadius: "4px",
            cursor: "pointer",
            bgcolor: (t) => alpha(t.palette.primary.main, 0.06),
            "&:hover": { bgcolor: (t) => alpha(t.palette.primary.main, 0.12) },
          }}
        >
          <Iconify icon="mdi:creation" width={12} color="primary.main" />
          <Typography sx={{ fontSize: 10, fontWeight: 600, color: "primary.main" }}>
            Fix with Falcon
          </Typography>
        </Box>
      )}
    </Box>
  );
};

EvalDetailExpansion.propTypes = {
  span: PropTypes.object.isRequired,
  evalConfigId: PropTypes.string,
  evalName: PropTypes.string,
  outputType: PropTypes.string,
  onFixWithFalcon: PropTypes.func,
  pl: PropTypes.number,
};

export default EvalDetailExpansion;
