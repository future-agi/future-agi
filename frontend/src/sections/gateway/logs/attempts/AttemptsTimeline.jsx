import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography } from "@mui/material";
import {
  formatCost,
  formatLatency,
  formatTokens,
} from "../../utils/formatters";
import {
  ATTEMPT_KINDS,
  ATTEMPT_OUTCOMES,
  ATTEMPT_REASONS,
  CREDENTIAL_TYPES,
  ERROR_CLASSES,
  colorOf,
  labelOf,
} from "../explorerLabels";

// The upstream attempts behind one request, in index order. Landed cost and
// fee rates are FutureAGI's own numbers and are never rendered, even for
// staff.

const MICROS_PER_USD = 1_000_000;

const count = (value) => {
  const n = Number(value);
  return value != null && Number.isFinite(n) && n > 0 ? n : 0;
};

const text = (value) => (value == null ? "" : String(value));

const percent = (part, whole) =>
  `${Math.max(1, Math.round((part / whole) * 100))}%`;

function TokenSummary({ attempt }) {
  const prompt = count(attempt.prompt_tokens);
  const completion = count(attempt.completion_tokens);
  if (!prompt && !completion) return null;

  const cached = count(attempt.cached_tokens);
  const cacheWrite = count(attempt.cache_write_tokens);
  const reasoning = count(attempt.reasoning_tokens);
  const promptParts = [
    cached && `${formatTokens(cached)} cached`,
    cacheWrite && `${formatTokens(cacheWrite)} cache write`,
  ].filter(Boolean);

  return (
    <Typography variant="caption" color="text.secondary">
      {formatTokens(prompt)} in
      {promptParts.length > 0 && ` (${promptParts.join(", ")})`}
      {" · "}
      {formatTokens(completion)} out
      {reasoning > 0 && ` (${formatTokens(reasoning)} reasoning)`}
    </Typography>
  );
}

TokenSummary.propTypes = { attempt: PropTypes.object.isRequired };

function LatencyBar({ latency, ttfb, maxLatency }) {
  return (
    <Stack direction="row" spacing={1} alignItems="center">
      <Box
        aria-hidden
        sx={{
          flex: 1,
          maxWidth: 240,
          height: 6,
          borderRadius: 3,
          bgcolor: "action.hover",
        }}
      >
        <Box
          data-latency-bar
          style={{ width: percent(latency, maxLatency) }}
          sx={{ height: "100%", borderRadius: 3, bgcolor: "primary.light" }}
        >
          {ttfb > 0 && (
            <Box
              data-ttfb-bar
              style={{ width: percent(Math.min(ttfb, latency), latency) }}
              sx={{ height: "100%", borderRadius: 3, bgcolor: "primary.dark" }}
            />
          )}
        </Box>
      </Box>
      <Typography variant="caption" color="text.secondary" noWrap>
        {ttfb > 0 && `TTFB ${formatLatency(ttfb)} · `}
        Total {formatLatency(latency)}
      </Typography>
    </Stack>
  );
}

LatencyBar.propTypes = {
  latency: PropTypes.number.isRequired,
  ttfb: PropTypes.number.isRequired,
  maxLatency: PropTypes.number.isRequired,
};

function BillingMark({ attempt }) {
  if (attempt.outcome === "skipped") {
    return <Chip size="small" variant="outlined" label="Not sent" />;
  }
  return attempt.billable ? (
    <Chip size="small" variant="outlined" color="success" label="Billed" />
  ) : (
    <Chip size="small" variant="outlined" label="Not billed" />
  );
}

BillingMark.propTypes = { attempt: PropTypes.object.isRequired };

function AttemptRow({ attempt, maxLatency }) {
  const kind = text(attempt.kind);
  const outcome = text(attempt.outcome);
  const provider = text(attempt.provider);
  const model = text(attempt.model);
  const endpoint = text(attempt.endpoint_id);
  const credential = text(attempt.credential_type);
  const errorClass = text(attempt.error_class);
  const origin = text(attempt.error_origin);
  const reason = text(attempt.reason);
  const status = count(attempt.status_code);
  const latency = count(attempt.latency_ms);
  const sent = outcome !== "skipped";
  const setAside = outcome === "discarded" || outcome === "skipped";

  return (
    <Box
      component="li"
      data-outcome={outcome}
      sx={{
        p: 1.5,
        border: 1,
        borderColor: "divider",
        borderStyle: setAside ? "dashed" : "solid",
        borderRadius: 1,
        opacity: setAside ? 0.75 : 1,
      }}
    >
      <Stack spacing={0.5}>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
        >
          <Typography variant="caption" color="text.secondary">
            #{attempt.index}
          </Typography>
          {kind && (
            <Chip
              size="small"
              label={labelOf(ATTEMPT_KINDS, kind)}
              color={colorOf(ATTEMPT_KINDS, kind)}
            />
          )}
          <Typography
            variant="body2"
            sx={{ fontFamily: "monospace", fontSize: "0.8rem", flex: 1 }}
          >
            {[provider, model].filter(Boolean).join(" / ") || "—"}
          </Typography>
          {outcome && (
            <Chip
              size="small"
              variant="outlined"
              label={labelOf(ATTEMPT_OUTCOMES, outcome)}
              color={colorOf(ATTEMPT_OUTCOMES, outcome)}
            />
          )}
          <BillingMark attempt={attempt} />
        </Stack>

        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
        >
          {endpoint && (
            <Typography variant="caption" color="text.secondary">
              Endpoint {endpoint}
            </Typography>
          )}
          {credential && (
            <Chip
              size="small"
              variant="outlined"
              label={labelOf(CREDENTIAL_TYPES, credential)}
            />
          )}
          {status > 0 && (
            <Typography variant="caption" color="text.secondary">
              Status {status}
            </Typography>
          )}
          {errorClass && (
            <Typography variant="caption" color="error.main">
              {labelOf(ERROR_CLASSES, errorClass)}
              {origin && ` (${origin})`}
            </Typography>
          )}
          {reason && (
            <Typography variant="caption" color="text.secondary">
              Reason: {labelOf(ATTEMPT_REASONS, reason)}
            </Typography>
          )}
        </Stack>

        {text(attempt.error_message) && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ wordBreak: "break-word" }}
          >
            {attempt.error_message}
          </Typography>
        )}

        {latency > 0 && (
          <LatencyBar
            latency={latency}
            ttfb={count(attempt.ttfb_ms)}
            maxLatency={maxLatency}
          />
        )}

        <Stack
          direction="row"
          spacing={1.5}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
        >
          <TokenSummary attempt={attempt} />
          {sent && (
            <Typography variant="caption" color="text.secondary">
              Upstream{" "}
              {formatCost(count(attempt.upstream_cost_micros) / MICROS_PER_USD)}
              {" · "}Billed{" "}
              {formatCost(count(attempt.billed_cost_micros) / MICROS_PER_USD)}
            </Typography>
          )}
          {attempt.usage_estimated && (
            <Typography variant="caption" color="warning.dark">
              Estimated usage
            </Typography>
          )}
        </Stack>
      </Stack>
    </Box>
  );
}

AttemptRow.propTypes = {
  attempt: PropTypes.object.isRequired,
  maxLatency: PropTypes.number.isRequired,
};

export function AttemptsTimeline({ attempts }) {
  if (!Array.isArray(attempts) || attempts.length === 0) return null;

  const ordered = [...attempts].sort((a, b) => count(a.index) - count(b.index));
  const maxLatency = Math.max(...ordered.map((a) => count(a.latency_ms)));

  return (
    <Box>
      <Typography variant="subtitle2" gutterBottom>
        Attempts ({ordered.length})
      </Typography>
      <Stack
        component="ol"
        aria-label="Attempts"
        spacing={1}
        sx={{ listStyle: "none", m: 0, p: 0 }}
      >
        {ordered.map((attempt) => (
          <AttemptRow
            key={attempt.attempt_id || attempt.index}
            attempt={attempt}
            maxLatency={maxLatency}
          />
        ))}
      </Stack>
    </Box>
  );
}

AttemptsTimeline.propTypes = {
  attempts: PropTypes.arrayOf(PropTypes.object),
};

export default AttemptsTimeline;
