import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  Collapse,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

// ─────────────────────────────────────────────────────────────────────────────
// Formatters (mirrors CallAnalyticsView conventions so the two drawers
// render values identically — e.g. "4.5s", "450ms", "$0.02").
// ─────────────────────────────────────────────────────────────────────────────

const fmtDuration = (seconds) => {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "—";
  const n = Number(seconds);
  if (n < 60) return `${n.toFixed(1)}s`;
  const m = Math.floor(n / 60);
  const s = Math.round(n % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
};

const fmtMs = (ms) => {
  if (ms == null || !Number.isFinite(Number(ms))) return "—";
  const n = Number(ms);
  if (n >= 1000) return `${(n / 1000).toFixed(2)}s`;
  return `${Math.round(n)}ms`;
};

const fmtMoney = (n) => {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v === 0) return "$0.00";
  return `$${v.toFixed(v < 0.1 ? 4 : 2)}`;
};

const fmtNumber = (n) => {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
};

// ─────────────────────────────────────────────────────────────────────────────
// Shared section header — same visual language as CallAnalyticsView.
// Duplicated rather than extracted for now; if a third drawer ever needs
// these, lift them into a shared kpi.jsx.
// ─────────────────────────────────────────────────────────────────────────────

const SectionLabel = ({ children }) => (
  <Typography
    sx={{
      fontSize: 10,
      fontWeight: 600,
      color: "text.secondary",
      textTransform: "uppercase",
      letterSpacing: "0.06em",
    }}
  >
    {children}
  </Typography>
);

SectionLabel.propTypes = { children: PropTypes.node };

const KpiCell = ({ label, value, hint, tone = "default" }) => {
  const toneColor = {
    default: "text.primary",
    success: "success.main",
    warn: (theme) =>
      theme.palette.mode === "dark"
        ? theme.palette.warning.main
        : theme.palette.warning.darker,
    danger: "error.main",
  }[tone];
  return (
    <Tooltip
      title={hint || ""}
      placement="top"
      arrow
      disableHoverListener={!hint}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 0.25,
          py: 0.75,
        }}
      >
        <Typography
          sx={{
            fontSize: 10.5,
            fontWeight: 600,
            color: "text.secondary",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            whiteSpace: "nowrap",
          }}
        >
          {label}
        </Typography>
        <Typography
          sx={{
            fontSize: 16,
            fontWeight: 700,
            color: toneColor,
            lineHeight: 1.2,
            whiteSpace: "nowrap",
            fontFamily: "monospace",
          }}
        >
          {value}
        </Typography>
      </Box>
    </Tooltip>
  );
};

KpiCell.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node.isRequired,
  hint: PropTypes.string,
  tone: PropTypes.oneOf(["default", "success", "warn", "danger"]),
};

// ─────────────────────────────────────────────────────────────────────────────
// Chat-specific KPI derivations
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Compute chat KPIs from the detail payload. Multiple fallbacks because
 * the chat serializer (TH-4525) is in flux — different deployments ship
 * tokens/cost/latency on different keys. Every derived value can be null
 * and `KpiCell` renders "—" gracefully.
 */
const useChatMetrics = (data) => {
  return useMemo(() => {
    const transcript = Array.isArray(data?.transcript) ? data.transcript : [];
    const nonSystem = transcript.filter((t) => t.speakerRole !== "system");

    const turnCount =
      data?.turn_count ??
      data?.turnCount ??
      (nonSystem.length > 0 ? nonSystem.length : null);

    // Tokens — try aggregates first, fall back to summing per-turn values.
    const pickTokenSum = (keys) => {
      let total = 0;
      let found = false;
      for (const t of transcript) {
        for (const k of keys) {
          const v = t?.[k];
          if (v != null && Number.isFinite(Number(v))) {
            total += Number(v);
            found = true;
            break;
          }
        }
      }
      return found ? total : null;
    };

    const tokensIn =
      data?.input_tokens ??
      data?.inputTokens ??
      data?.prompt_tokens ??
      data?.promptTokens ??
      pickTokenSum(["input_tokens", "inputTokens", "prompt_tokens"]);

    const tokensOut =
      data?.output_tokens ??
      data?.outputTokens ??
      data?.completion_tokens ??
      data?.completionTokens ??
      pickTokenSum(["output_tokens", "outputTokens", "completion_tokens"]);

    const totalTokens =
      data?.total_tokens ??
      data?.totalTokens ??
      ((tokensIn ?? 0) + (tokensOut ?? 0) || null);

    // Cost — direct field preferred; otherwise try the breakdown total.
    const cost =
      data?.cost ??
      data?.total_cost ??
      data?.totalCost ??
      data?.customer_cost_breakdown?.total ??
      null;

    // Avg per-turn latency — prefer direct aggregate, else average
    // per-turn latency values that exist on the transcript.
    const avgLatency =
      data?.avg_agent_latency_ms ??
      data?.avg_latency_ms ??
      data?.avgLatencyMs ??
      (() => {
        const latencies = nonSystem
          .map((t) => t?.latency_ms ?? t?.latencyMs ?? t?.response_time_ms)
          .filter((v) => v != null && Number.isFinite(Number(v)))
          .map(Number);
        if (latencies.length === 0) return null;
        return latencies.reduce((a, b) => a + b, 0) / latencies.length;
      })();

    const duration =
      data?.duration_seconds ??
      data?.duration ??
      data?.durationSeconds ??
      null;

    return {
      duration,
      turnCount,
      tokensIn,
      tokensOut,
      totalTokens,
      cost,
      avgLatency,
    };
  }, [data]);
};

// ─────────────────────────────────────────────────────────────────────────────
// KPI strip
// ─────────────────────────────────────────────────────────────────────────────

const ChatKpiStrip = ({ metrics }) => {
  const {
    duration,
    turnCount,
    tokensIn,
    tokensOut,
    totalTokens,
    cost,
    avgLatency,
  } = metrics;

  const cells = [
    { label: "Duration", value: fmtDuration(duration), hint: "Chat length" },
    { label: "Turns", value: fmtNumber(turnCount), hint: "Number of turns" },
    {
      label: "Tokens In",
      value: fmtNumber(tokensIn),
      hint: "Input / prompt tokens",
    },
    {
      label: "Tokens Out",
      value: fmtNumber(tokensOut),
      hint: "Output / completion tokens",
    },
    {
      label: "Total Tokens",
      value: fmtNumber(totalTokens),
      hint: "All tokens consumed",
    },
    {
      label: "Avg Latency",
      value: fmtMs(avgLatency),
      hint: "Avg per-turn latency",
    },
    { label: "Cost", value: fmtMoney(cost), hint: "Total call cost" },
  ];

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(100px, 1fr))",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        bgcolor: "background.paper",
        overflow: "hidden",
        "& > *": {
          bgcolor: "background.paper",
          px: 1.25,
          borderRight: "1px solid",
          borderBottom: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      {cells.map((c) => (
        <KpiCell key={c.label} {...c} />
      ))}
    </Box>
  );
};

ChatKpiStrip.propTypes = { metrics: PropTypes.object.isRequired };

// ─────────────────────────────────────────────────────────────────────────────
// Collapsible AI summary card — same behavior as voice.
// ─────────────────────────────────────────────────────────────────────────────

const AiSummaryCard = ({ summary }) => {
  const [open, setOpen] = useState(false);
  if (!summary || typeof summary !== "string" || !summary.trim()) return null;

  return (
    <Stack gap={0.5}>
      <SectionLabel>AI summary</SectionLabel>
      <ButtonBase
        onClick={() => setOpen((v) => !v)}
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 0.75,
          px: 1.25,
          py: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          bgcolor: "background.paper",
          width: "100%",
          justifyContent: "flex-start",
          textAlign: "left",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon="mdi:creation"
          width={14}
          sx={{ color: "primary.main", flexShrink: 0 }}
        />
        <Typography
          sx={{
            flex: 1,
            minWidth: 0,
            fontSize: 11,
            color: "text.primary",
            lineHeight: 1.4,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: open ? "normal" : "nowrap",
          }}
        >
          {open ? summary : summary.slice(0, 140)}
          {!open && summary.length > 140 ? "…" : null}
        </Typography>
        <Iconify
          icon="mdi:chevron-down"
          width={14}
          sx={{
            color: "text.disabled",
            flexShrink: 0,
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 120ms",
          }}
        />
      </ButtonBase>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ height: 2 }} />
      </Collapse>
    </Stack>
  );
};

AiSummaryCard.propTypes = { summary: PropTypes.string };

// ─────────────────────────────────────────────────────────────────────────────
// Main view
// ─────────────────────────────────────────────────────────────────────────────

const ChatAnalyticsView = ({ data }) => {
  const metrics = useChatMetrics(data);

  const hasAnyMetric = Object.values(metrics).some((v) => v != null);
  const analysisSummary = data?.call_summary || data?.callSummary;
  const hasAny = hasAnyMetric || analysisSummary;

  if (!hasAny) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 160,
        }}
      >
        <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
          No chat analytics data available
        </Typography>
      </Box>
    );
  }

  return (
    <Stack gap={2}>
      {hasAnyMetric && <ChatKpiStrip metrics={metrics} />}
      <AiSummaryCard summary={analysisSummary} />
    </Stack>
  );
};

ChatAnalyticsView.propTypes = {
  data: PropTypes.object,
};

export default ChatAnalyticsView;
