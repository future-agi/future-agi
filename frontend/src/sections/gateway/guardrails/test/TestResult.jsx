import { formatExactMoney } from "src/sections/common/format/money";
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { GatewayStatusChip } from "../../components/frame";
import { OUTCOMES, outcomeLabel } from "./interpret-result";
import { GATEWAY_MONO } from "../../utils/fonts";

function Fact({ label, value }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography typography="s3" color="text.secondary">
        {label}
      </Typography>
      <Typography typography="s1" color="text.primary" noWrap>
        {value}
      </Typography>
    </Box>
  );
}

Fact.propTypes = { label: PropTypes.string, value: PropTypes.node };

const mono = {
  typography: "s2",
  fontFamily: GATEWAY_MONO,
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  backgroundColor: "action.hover",
  borderRadius: 1,
  p: 1.5,
  m: 0,
};

/** One test request's outcome, read from the gateway's own answer. */
export default function TestResult({ result }) {
  const outcome = OUTCOMES[result.outcome];
  const headerEntries = Object.entries(result.headers || {});
  return (
    <Box
      component="section"
      aria-label="Result"
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}
      >
        <GatewayStatusChip tone={outcome.tone} label={outcomeLabel(result)} />
      </Box>
      <Typography typography="s1" color="text.primary">
        {outcome.sentence(result)}
      </Typography>
      {result.outcome === "blocked" && result.reason && (
        <Typography typography="s2" color="text.secondary">
          {result.reason}
        </Typography>
      )}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
          gap: 1.5,
        }}
      >
        <Fact label="Model" value={result.model} />
        <Fact label="Provider" value={result.provider} />
        <Fact
          label="Latency"
          value={result.latencyMs === null ? null : `${result.latencyMs} ms`}
        />
        <Fact
          label="Cost"
          value={formatExactMoney(result.cost, {
            minimumFractionDigits: 6,
            missing: null,
          })}
        />
      </Box>
      {result.answer && (
        <Box>
          <Typography typography="s2" color="text.secondary" sx={{ mb: 0.5 }}>
            Answer
          </Typography>
          <Box
            component="pre"
            sx={{ ...mono, maxHeight: 240, overflow: "auto" }}
          >
            {result.answer}
          </Box>
        </Box>
      )}
      {result.requestId && (
        <Link
          component={RouterLink}
          href={`${paths.dashboard.gateway.logs}?request_id=${encodeURIComponent(result.requestId)}`}
          typography="s2"
          sx={{ alignSelf: "flex-start" }}
        >
          Open the request log
        </Link>
      )}
      {headerEntries.length > 0 && (
        <Box component="details">
          <Typography
            component="summary"
            typography="s2"
            color="text.secondary"
            sx={{ cursor: "pointer" }}
          >
            Gateway headers ({headerEntries.length})
          </Typography>
          <Box component="pre" sx={{ ...mono, mt: 1 }}>
            {headerEntries.map(([key, value]) => `${key}: ${value}`).join("\n")}
          </Box>
        </Box>
      )}
    </Box>
  );
}

TestResult.propTypes = {
  result: PropTypes.shape({
    outcome: PropTypes.oneOf(Object.keys(OUTCOMES)).isRequired,
    statusCode: PropTypes.number,
    reason: PropTypes.string,
    rule: PropTypes.string,
    model: PropTypes.string,
    provider: PropTypes.string,
    requestId: PropTypes.string,
    checks: PropTypes.arrayOf(PropTypes.string),
    latencyMs: PropTypes.number,
    cost: PropTypes.number,
    answer: PropTypes.string,
    headers: PropTypes.object,
  }).isRequired,
};
