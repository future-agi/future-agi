import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  Collapse,
  Stack,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { computeCallMetrics, enrichTurns } from "./transcriptUtils";

// ─────────────────────────────────────────────────────────────────────────────
// Formatting helpers
// ─────────────────────────────────────────────────────────────────────────────

const fmtDuration = (seconds) => {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
};

const fmtMs = (ms, { forceMs = false } = {}) => {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (!forceMs && ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
};

const fmtMoney = (n) => {
  if (n == null || !Number.isFinite(n)) return "$0.00";
  return `$${n.toFixed(n < 0.1 ? 3 : 2)}`;
};

const fmtNumber = (n) => {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
};

// ─────────────────────────────────────────────────────────────────────────────
// Section header
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

// ─────────────────────────────────────────────────────────────────────────────
// KPI strip
// ─────────────────────────────────────────────────────────────────────────────

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

const fmtWpm = (n) => {
  if (n == null || !Number.isFinite(n)) return "—";
  return String(Math.round(n));
};

const KpiStrip = ({ metrics, apiMetrics }) => {
  if (!metrics && !apiMetrics) return null;
  const m = metrics || {};
  const api = apiMetrics || {};

  const {
    duration,
    wordCount,
    userTalkPct,
    assistantTalkPct,
    silenceTotal,
    timeToFirstWord,
  } = m;

  const turnCount = api.turnCount ?? m.turnCount;
  const userInterrupts = api.userInterruptionCount;
  const aiInterrupts = api.aiInterruptionCount;
  const totalInterrupts = m.interruptionCount;
  const avgLatency = api.avgAgentLatencyMs;
  const userWpm = api.userWpm;
  const botWpm = api.botWpm;

  const talkRatioValue =
    userTalkPct != null && assistantTalkPct != null ? (
      <>
        <Box component="span" sx={{ color: "#E9690C" }}>
          {userTalkPct}
        </Box>
        <Box component="span" sx={{ color: "text.disabled" }}>
          {" / "}
        </Box>
        <Box component="span" sx={{ color: "primary.main" }}>
          {assistantTalkPct}
        </Box>
      </>
    ) : (
      "—"
    );

  // Collect all cells
  const cells = [
    {
      label: "Duration",
      value: fmtDuration(duration),
      hint: "Total call length",
    },
    { label: "Turns", value: fmtNumber(turnCount), hint: "Speech turns" },
    {
      label: "Latency",
      value: avgLatency != null ? fmtMs(avgLatency, { forceMs: true }) : "—",
      hint: "Avg agent response latency",
    },
    { label: "User / AI", value: talkRatioValue, hint: "Talk time split (%)" },
    {
      label: "Words",
      value: fmtNumber(wordCount),
      hint: "Combined word count",
    },
    {
      label: "Silence",
      value:
        silenceTotal > 0 ? fmtMs(silenceTotal * 1000, { forceMs: true }) : "0ms",
      hint: "Dead air (> 0.3s gaps)",
      tone: silenceTotal > 10 ? "warn" : "default",
    },
    {
      label: "TTFW",
      value:
        timeToFirstWord != null
          ? fmtMs(timeToFirstWord * 1000, { forceMs: true })
          : "—",
      hint: "Time to first word",
    },
  ];

  if (userInterrupts != null || aiInterrupts != null) {
    cells.push({
      label: "User Int.",
      value: String(userInterrupts ?? 0),
      hint: "User interrupted the agent",
      tone: (userInterrupts ?? 0) > 0 ? "warn" : "default",
    });
    cells.push({
      label: "AI Int.",
      value: String(aiInterrupts ?? 0),
      hint: "Agent interrupted the user",
      tone: (aiInterrupts ?? 0) > 0 ? "warn" : "default",
    });
  } else if (totalInterrupts != null) {
    cells.push({
      label: "Interrupts",
      value: String(totalInterrupts),
      hint: "Speaker overlaps",
      tone: totalInterrupts > 0 ? "warn" : "default",
    });
  }

  if (userWpm != null) {
    cells.push({
      label: "User WPM",
      value: fmtWpm(userWpm),
      hint: "User words per minute",
    });
  }
  if (botWpm != null) {
    cells.push({
      label: "Agent WPM",
      value: fmtWpm(botWpm),
      hint: "Agent words per minute",
    });
  }

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

KpiStrip.propTypes = {
  metrics: PropTypes.object,
  apiMetrics: PropTypes.object,
};

// ─────────────────────────────────────────────────────────────────────────────
// Latency pipeline
// ─────────────────────────────────────────────────────────────────────────────

const LATENCY_STAGES = [
  { key: "endpointing", label: "Endpointing" },
  { key: "transcriber", label: "Transcriber" },
  { key: "model", label: "LLM" },
  { key: "voice", label: "Voice" },
];

const LatencyPipeline = ({ latencies }) => {
  const theme = useTheme();

  const stages = useMemo(() => {
    if (!latencies) return [];
    return LATENCY_STAGES.filter(
      (s) => latencies[s.key] != null && Number.isFinite(latencies[s.key]),
    ).map((s) => ({ ...s, value: latencies[s.key] }));
  }, [latencies]);

  const total = useMemo(
    () => stages.reduce((sum, s) => sum + s.value, 0),
    [stages],
  );

  if (stages.length === 0) return null;

  // 4-shade blue ramp — darkest for the first stage so the eye reads the
  // pipeline left→right the same way it reads the labels.
  const colorForIndex = (i) => {
    const shades = [
      theme.palette.blue?.[700] || theme.palette.primary.dark,
      theme.palette.blue?.[600] || theme.palette.primary.main,
      theme.palette.blue?.[500] || theme.palette.primary.light,
      theme.palette.blue?.[400] || "#8BB5FF",
    ];
    return shades[i] || theme.palette.primary.main;
  };

  return (
    <Stack gap={0.75}>
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <SectionLabel>Latency pipeline</SectionLabel>
        <Typography
          sx={{
            fontSize: 10,
            color: "text.secondary",
            fontFamily: "monospace",
          }}
        >
          Total&nbsp;
          <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
            {fmtMs(total)}
          </Box>
        </Typography>
      </Stack>

      {/* The stacked bar */}
      <Box
        sx={{
          display: "flex",
          height: 22,
          width: "100%",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          overflow: "hidden",
        }}
      >
        {stages.map((s, i) => {
          const pct = total > 0 ? (s.value / total) * 100 : 0;
          const showInline = pct >= 12;
          return (
            <Tooltip
              key={s.key}
              arrow
              placement="top"
              title={
                <Box>
                  <Typography sx={{ fontSize: 11, fontWeight: 600 }}>
                    {s.label}
                  </Typography>
                  <Typography sx={{ fontSize: 10 }}>
                    {fmtMs(s.value)} ({pct.toFixed(1)}% of total)
                  </Typography>
                </Box>
              }
            >
              <Box
                sx={{
                  width: `${pct}%`,
                  bgcolor: colorForIndex(i),
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#fff",
                  fontSize: 10,
                  fontWeight: 600,
                  fontFamily: "monospace",
                  transition: "filter 120ms",
                  "&:hover": { filter: "brightness(1.08)" },
                  borderRight:
                    i < stages.length - 1
                      ? "1px solid rgba(0,0,0,0.15)"
                      : "none",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                }}
              >
                {showInline ? fmtMs(s.value) : null}
              </Box>
            </Tooltip>
          );
        })}
      </Box>

      {/* Legend row */}
      <Stack direction="row" gap={1.5} sx={{ flexWrap: "wrap" }}>
        {stages.map((s, i) => (
          <Stack
            key={s.key}
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ minWidth: 0 }}
          >
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: "2px",
                bgcolor: colorForIndex(i),
                flexShrink: 0,
              }}
            />
            <Typography
              sx={{
                fontSize: 10,
                color: "text.secondary",
                whiteSpace: "nowrap",
              }}
            >
              {s.label}
            </Typography>
            <Typography
              sx={{
                fontSize: 10,
                color: "text.primary",
                fontWeight: 600,
                fontFamily: "monospace",
              }}
            >
              {fmtMs(s.value)}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Stack>
  );
};

LatencyPipeline.propTypes = { latencies: PropTypes.object };

// ─────────────────────────────────────────────────────────────────────────────
// Cost breakdown — three-row layout
// ─────────────────────────────────────────────────────────────────────────────

const COST_STAGES = [
  { key: "stt", label: "Speech to Text", icon: "mdi:microphone-outline" },
  { key: "llm", label: "LLM", icon: "mdi:brain" },
  { key: "tts", label: "Text to Speech", icon: "mdi:volume-high" },
];

const CostBreakdown = ({ costBreakdown }) => {
  const theme = useTheme();

  const rows = useMemo(() => {
    if (!costBreakdown) return [];
    return COST_STAGES.map((s) => {
      const entry = costBreakdown[s.key];
      return {
        ...s,
        cost: entry?.cost ?? 0,
        promptTokens: entry?.promptTokens,
        completionTokens: entry?.completionTokens,
      };
    }).filter((r) => r.cost > 0 || r.promptTokens || r.completionTokens);
  }, [costBreakdown]);

  const total = rows.reduce((sum, r) => sum + r.cost, 0);

  if (rows.length === 0) return null;

  return (
    <Stack gap={0.75}>
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <SectionLabel>Cost breakdown</SectionLabel>
        <Typography
          sx={{
            fontSize: 10,
            color: "text.secondary",
            fontFamily: "monospace",
          }}
        >
          Total&nbsp;
          <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
            {fmtMoney(total)}
          </Box>
        </Typography>
      </Stack>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          overflow: "hidden",
        }}
      >
        {rows.map((row, i) => {
          const pct = total > 0 ? (row.cost / total) * 100 : 0;
          return (
            <Box
              key={row.key}
              sx={{
                display: "grid",
                gridTemplateColumns: "auto 1fr auto",
                alignItems: "center",
                gap: 1,
                px: 1,
                py: 0.75,
                borderBottom: i < rows.length - 1 ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                gap={0.75}
                sx={{ minWidth: 110 }}
              >
                <Iconify
                  icon={row.icon}
                  width={14}
                  sx={{ color: "text.secondary" }}
                />
                <Typography sx={{ fontSize: 11, fontWeight: 600 }}>
                  {row.label}
                </Typography>
              </Stack>
              <Box sx={{ minWidth: 0 }}>
                <Box
                  sx={{
                    height: 5,
                    borderRadius: "3px",
                    bgcolor: "action.hover",
                    overflow: "hidden",
                  }}
                >
                  <Box
                    sx={{
                      height: "100%",
                      width: `${pct}%`,
                      bgcolor:
                        theme.palette.blue?.[500] || theme.palette.primary.main,
                      transition: "width 200ms",
                    }}
                  />
                </Box>
                {(row.promptTokens != null || row.completionTokens != null) && (
                  <Typography
                    sx={{
                      fontSize: 9.5,
                      color: "text.disabled",
                      mt: 0.25,
                      fontFamily: "monospace",
                    }}
                  >
                    {row.promptTokens != null &&
                      `${fmtNumber(row.promptTokens)} in`}
                    {row.promptTokens != null &&
                      row.completionTokens != null &&
                      " · "}
                    {row.completionTokens != null &&
                      `${fmtNumber(row.completionTokens)} out`}
                  </Typography>
                )}
              </Box>
              <Stack
                direction="column"
                alignItems="flex-end"
                sx={{ minWidth: 70 }}
              >
                <Typography
                  sx={{
                    fontSize: 11.5,
                    fontWeight: 600,
                    color: "text.primary",
                    fontFamily: "monospace",
                  }}
                >
                  {fmtMoney(row.cost)}
                </Typography>
                <Typography
                  sx={{
                    fontSize: 9.5,
                    color: "text.disabled",
                    fontFamily: "monospace",
                  }}
                >
                  {pct.toFixed(0)}%
                </Typography>
              </Stack>
            </Box>
          );
        })}
      </Box>
    </Stack>
  );
};

CostBreakdown.propTypes = { costBreakdown: PropTypes.object };

// ─────────────────────────────────────────────────────────────────────────────
// AI Summary — collapsible card
// ─────────────────────────────────────────────────────────────────────────────

const AiSummaryCard = ({ summary }) => {
  const [open, setOpen] = useState(false);
  if (!summary) return null;
  return (
    <Stack gap={0.75}>
      <SectionLabel>AI summary</SectionLabel>
      <ButtonBase
        onClick={() => setOpen((o) => !o)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1,
          py: 0.75,
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

const CallAnalyticsView = ({
  transcript,
  latencies,
  analysisSummary,
  costBreakdown,
  isLiveKit,
  apiMetrics,
}) => {
  const metrics = useMemo(() => {
    const turns = enrichTurns(transcript);
    return computeCallMetrics(turns);
  }, [transcript]);

  const hasApiMetrics =
    apiMetrics && Object.values(apiMetrics).some((v) => v != null);

  const hasAny =
    (transcript && transcript.length > 0) ||
    latencies ||
    costBreakdown ||
    analysisSummary ||
    hasApiMetrics;

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
          No call analytics data available
        </Typography>
      </Box>
    );
  }

  return (
    <Stack gap={2}>
      {/* KPI strip — always render if we have transcript or API metrics */}
      {(transcript?.length > 0 || hasApiMetrics) && (
        <KpiStrip metrics={metrics} apiMetrics={apiMetrics} />
      )}

      {/* Latency pipeline */}
      {!isLiveKit && <LatencyPipeline latencies={latencies} />}

      {/* Cost breakdown */}
      {!isLiveKit && <CostBreakdown costBreakdown={costBreakdown} />}

      {/* LiveKit note — we can't surface their internal pipeline */}
      {isLiveKit && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            p: 1,
            bgcolor: "background.neutral",
          }}
        >
          <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
            System metrics and cost breakdown are not available for LiveKit
            agents — LiveKit does not expose internal pipeline metrics or
            billing data via API.
          </Typography>
        </Box>
      )}

      {/* AI summary */}
      <AiSummaryCard summary={analysisSummary} />
    </Stack>
  );
};

CallAnalyticsView.propTypes = {
  transcript: PropTypes.array,
  latencies: PropTypes.shape({
    model: PropTypes.number,
    voice: PropTypes.number,
    transcriber: PropTypes.number,
    endpointing: PropTypes.number,
  }),
  analysisSummary: PropTypes.string,
  costBreakdown: PropTypes.object,
  isLiveKit: PropTypes.bool,
  apiMetrics: PropTypes.shape({
    turnCount: PropTypes.number,
    talkRatio: PropTypes.number,
    agentTalkPercentage: PropTypes.number,
    avgAgentLatencyMs: PropTypes.number,
    userWpm: PropTypes.number,
    botWpm: PropTypes.number,
    userInterruptionCount: PropTypes.number,
    aiInterruptionCount: PropTypes.number,
  }),
};

export default CallAnalyticsView;
