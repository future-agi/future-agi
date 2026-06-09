import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Markdown from "react-markdown";
import Iconify from "src/components/iconify";
import EvalErrorLocalization from "../EvalErrorLocalization";
import { isPassed } from "./utils";

// Expansion content for one eval result — explanation + error localization + Fix with Falcon.
const EvalDetailExpansion = ({ row, onFixWithFalcon, pl = 4.5 }) => {
  const explanation = row.explanation || row.eval_explanation;
  const observationSpanId =
    row.observation_span_id || row.observationSpanId || row.spanId;
  const customEvalConfigId =
    row.custom_eval_config_id || row.eval_config_id || row.evalConfigId;
  const hasLoc = !!(observationSpanId && customEvalConfigId);

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
          observationSpanId={observationSpanId}
          customEvalConfigId={customEvalConfigId}
          initialAnalysis={row.error_analysis || null}
          initialStatus={row.error_localizer_status || null}
        />
      )}
      {!isPassed(row) && onFixWithFalcon && (
        <Box
          onClick={(e) => {
            e.stopPropagation();
            onFixWithFalcon({ level: "eval", ev: row });
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
  row: PropTypes.object.isRequired,
  onFixWithFalcon: PropTypes.func,
  pl: PropTypes.number,
};

export default EvalDetailExpansion;
