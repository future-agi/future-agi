import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { fShortenNumber } from "src/utils/format-number";

const BreakdownTooltip = ({ promptTokens, completionTokens, totalTokens }) => (
  <Box sx={{ p: 0.5, minWidth: 160 }}>
    <Typography sx={{ fontSize: 13, fontWeight: 600, mb: 1 }}>
      Usage breakdown
    </Typography>
    <BRow
      label="Input tokens"
      value={promptTokens}
      icon="solar:arrow-down-linear"
      bold
    />
    <Box sx={{ my: 0.75 }} />
    <BRow
      label="Output tokens"
      value={completionTokens}
      icon="solar:arrow-up-linear"
      bold
    />
    <Box sx={{ borderTop: "1px solid rgba(255,255,255,0.15)", my: 0.75 }} />
    <BRow label="Total tokens" value={totalTokens} bold />
  </Box>
);

BreakdownTooltip.propTypes = {
  promptTokens: PropTypes.number,
  completionTokens: PropTypes.number,
  totalTokens: PropTypes.number,
};

const BRow = ({ label, value, icon, bold }) => (
  <Box
    sx={{
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      py: 0.1,
      gap: 2,
    }}
  >
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
      <Typography
        sx={{
          fontSize: 12,
          fontWeight: bold ? 600 : 400,
          color: bold ? "inherit" : "text.secondary",
        }}
      >
        {label}
      </Typography>
      {icon && <Iconify icon={icon} width={12} />}
    </Box>
    <Typography
      sx={{
        fontSize: 12,
        fontWeight: bold ? 600 : 400,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {(value ?? 0).toLocaleString()}
    </Typography>
  </Box>
);

BRow.propTypes = {
  label: PropTypes.string,
  value: PropTypes.number,
  icon: PropTypes.string,
  bold: PropTypes.bool,
};

const VoiceTokenCell = (params) => {
  const data = params?.data;

  // Voice calls store tokens under gen_ai.usage.* (dot-notation span attrs)
  // or under the standard prompt_tokens / completion_tokens keys.
  const promptTokens =
    Number(data?.["gen_ai.usage.input_tokens"]) ||
    Number(data?.prompt_tokens) ||
    Number(data?.["cost_breakdown.llmPromptTokens"]) ||
    0;
  const completionTokens =
    Number(data?.["gen_ai.usage.output_tokens"]) ||
    Number(data?.completion_tokens) ||
    Number(data?.["cost_breakdown.llmCompletionTokens"]) ||
    0;
  const totalTokens =
    Number(data?.["gen_ai.usage.total_tokens"]) ||
    Number(data?.total_tokens) ||
    promptTokens + completionTokens;

  if (!totalTokens && !promptTokens && !completionTokens) {
    return (
      <Typography
        variant="body2"
        sx={{
          fontSize: 13,
          color: "text.disabled",
          px: 1.5,
          width: "100%",
          textAlign: "right",
        }}
      >
        -
      </Typography>
    );
  }

  const hasBreakdown = promptTokens > 0 || completionTokens > 0;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-end",
        width: "100%",
        px: 1.5,
        height: "100%",
        gap: 0.5,
      }}
    >
      {hasBreakdown ? (
        <Box
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.25,
            whiteSpace: "nowrap",
            fontSize: 13,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          <Typography component="span" sx={{ fontSize: 13 }}>
            {fShortenNumber(promptTokens)}
          </Typography>
          <Iconify
            icon="solar:arrow-down-linear"
            width={12}
            sx={{ color: "text.secondary" }}
          />
          <Box component="span" sx={{ mx: 0.25 }} />
          <Typography component="span" sx={{ fontSize: 13 }}>
            {fShortenNumber(completionTokens)}
          </Typography>
          <Iconify
            icon="solar:arrow-up-linear"
            width={12}
            sx={{ color: "text.secondary" }}
          />
          <Typography
            component="span"
            sx={{ fontSize: 13, color: "text.secondary", ml: 0.5 }}
          >
            (Σ {fShortenNumber(totalTokens)})
          </Typography>
        </Box>
      ) : (
        <Typography
          variant="body2"
          sx={{
            fontSize: 13,
            fontVariantNumeric: "tabular-nums",
            whiteSpace: "nowrap",
          }}
        >
          {totalTokens.toLocaleString()}
        </Typography>
      )}
      {hasBreakdown && (
        <Tooltip
          classes={{ tooltip: "ds-hovercard" }}
          title={
            <BreakdownTooltip
              promptTokens={promptTokens}
              completionTokens={completionTokens}
              totalTokens={totalTokens}
            />
          }
          arrow
          placement="bottom"
          slotProps={{
            tooltip: { sx: { maxWidth: 280 } },
          }}
        >
          <Box
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
            sx={{
              display: "flex",
              cursor: "default",
              opacity: 0.4,
              "&:hover": { opacity: 1 },
            }}
          >
            <Iconify
              icon="solar:info-circle-linear"
              width={14}
              color="text.secondary"
            />
          </Box>
        </Tooltip>
      )}
    </Box>
  );
};

VoiceTokenCell.propTypes = { data: PropTypes.object };

export default React.memo(VoiceTokenCell);
