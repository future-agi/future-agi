import { Box, Typography } from "@mui/material";
import React, { useMemo } from "react";
import RenderMeta from "../RenderMeta";
import PropTypes from "prop-types";

const choicesFontColourMap = {
  neutral: "text.primary",
  pass: "status.success.fg",
  fail: "status.danger.fg",
};

const EvaluateArrayCellRenderer = ({
  meta,
  isFutureAgiEval,
  value,
  choicesMap,
}) => {
  const finalArray = useMemo(() => {
    try {
      if (Array.isArray(value)) return value;
      if (typeof value !== "string") return [];
      const trimmed = value.trim();
      if (!trimmed) return [];

      if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
        const parsed = JSON.parse(trimmed.replaceAll("'", '"'));
        return Array.isArray(parsed) ? parsed : [];
      }

      if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
        const parsed = JSON.parse(trimmed.replaceAll("'", '"'));
        if (parsed && typeof parsed === "object") {
          if (Array.isArray(parsed.choices)) {
            return parsed.choices.map((c) => String(c));
          }
          if (parsed.choices != null) return [String(parsed.choices)];
          if (parsed.choice != null) return [String(parsed.choice)];
        }
        return [];
      }

      return [trimmed];
    } catch (e) {
      return typeof value === "string" && value.trim() ? [value.trim()] : [];
    }
  }, [value]);

  return (
    <Box
      sx={{
        padding: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        height: "100%",
      }}
    >
      <Box
        sx={{
          lineHeight: "1.5",
          display: "flex",
          gap: 1,
          flexWrap: "wrap",
          overflow: "auto",
        }}
      >
        {/* Empty result renders nothing (it means "no result yet", not "None"). */}
        {finalArray?.length
          ? finalArray?.map((item, idx) => (
              <Typography
                key={item}
                component="span"
                sx={{
                  color:
                    choicesFontColourMap?.[choicesMap?.[item] ?? "neutral"],
                  fontWeight: 400,
                  typography: "s3",
                }}
              >
                {idx > 0 && (
                  <Box component="span" sx={{ color: "text.tertiary", mr: 1 }}>
                    ·
                  </Box>
                )}
                {item}
              </Typography>
            ))
          : null}
      </Box>
      <RenderMeta
        originType="evaluation"
        meta={meta}
        showToken={!isFutureAgiEval}
      />
    </Box>
  );
};

EvaluateArrayCellRenderer.propTypes = {
  meta: PropTypes.object,
  isFutureAgiEval: PropTypes.bool,
  value: PropTypes.any,
  choicesMap: PropTypes.object,
};

export default EvaluateArrayCellRenderer;
