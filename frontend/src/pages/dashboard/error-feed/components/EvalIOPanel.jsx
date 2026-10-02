import React, { useMemo } from "react";
import { Box, Stack, Typography, alpha, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

// ── Format helpers ──────────────────────────────────────────────────────────
function stringify(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

// ── Single labelled blob (Input or Output) ──────────────────────────────────
function IOBlock({ label, icon, value }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const text = stringify(value);
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.02),
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{
          px: 1.25,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.015),
        }}
      >
        <Iconify icon={icon} width={13} sx={{ color: "text.secondary" }} />
        <Typography
          variant="s3"
          fontWeight={700}
          color="text.secondary"
          sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
        >
          {label}
        </Typography>
      </Stack>
      <Box sx={{ p: 1.25, maxHeight: 260, overflowY: "auto" }}>
        {text ? (
          <Typography
            variant="s2"
            component="pre"
            sx={{
              fontFamily:
                'ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace',
              lineHeight: 1.55,
              color: "text.primary",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              m: 0,
            }}
          >
            {text}
          </Typography>
        ) : (
          <Typography
            variant="s2"
            color="text.disabled"
            sx={{ fontStyle: "italic" }}
          >
            No {label.toLowerCase()} captured.
          </Typography>
        )}
      </Box>
    </Box>
  );
}
IOBlock.propTypes = {
  label: PropTypes.string.isRequired,
  icon: PropTypes.string.isRequired,
  value: PropTypes.any,
};

// ── Judge reason card (judge reason card) ────────
function JudgeReasonCard({ reason, score }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const failed = score != null && score < 1;
  const scoreColor = failed
    ? theme.palette.error.main
    : theme.palette.success.main;
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        p: 1.5,
        bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.02),
      }}
    >
      <Stack direction="row" alignItems="center" gap={0.75} sx={{ mb: 0.75 }}>
        <Iconify
          icon="mdi:scale-balance"
          width={13}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          variant="s3"
          fontWeight={700}
          color="text.secondary"
          sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
        >
          Evaluator reasoning
        </Typography>
        <Box sx={{ flex: 1 }} />
        {score != null && (
          <Box
            sx={{
              px: 0.75,
              py: 0.15,
              borderRadius: "4px",
              bgcolor: alpha(scoreColor, isDark ? 0.16 : 0.12),
            }}
          >
            <Typography
              variant="s3"
              fontWeight={700}
              sx={{ color: scoreColor, fontFeatureSettings: "'tnum'" }}
            >
              {Number(score).toFixed(2)} / 1.00
            </Typography>
          </Box>
        )}
      </Stack>
      <Typography
        variant="s2_1"
        color="text.primary"
        sx={{ lineHeight: 1.6, whiteSpace: "pre-wrap" }}
      >
        {reason || (
          <Box
            component="span"
            sx={{ color: "text.disabled", fontStyle: "italic" }}
          >
            No evaluator reasoning recorded.
          </Box>
        )}
      </Typography>
    </Box>
  );
}
JudgeReasonCard.propTypes = {
  reason: PropTypes.string,
  score: PropTypes.number,
};

// ── Main: EvalIOPanel ───────────────────────────────────────────────────────
export default function EvalIOPanel({ trace, evalScore }) {
  // evidence.* carries input/output/judgeReason/score from the serializer.
  // Axios bridge adds camelCase aliases on top of snake_case wire fields.
  const { input, output, judgeReason, score } = useMemo(() => {
    const ev = trace?.evidence ?? {};
    return {
      input: ev.input ?? trace?.input ?? null,
      output: ev.output ?? trace?.output ?? null,
      judgeReason: ev.judge_reason ?? trace?.judge_reason ?? null,
      score: evalScore ?? ev.score ?? null,
    };
  }, [trace, evalScore]);

  return (
    <Stack gap={1.25}>
      <IOBlock label="Input" icon="mdi:arrow-down-bold-outline" value={input} />
      <IOBlock label="Output" icon="mdi:arrow-up-bold-outline" value={output} />
      <JudgeReasonCard
        reason={judgeReason}
        score={score != null ? Number(score) : null}
      />
    </Stack>
  );
}
EvalIOPanel.propTypes = {
  trace: PropTypes.object,
  evalScore: PropTypes.number,
};
