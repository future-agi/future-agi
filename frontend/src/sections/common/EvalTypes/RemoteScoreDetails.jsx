// W7-4 remote scorers: a remote cell's call, on the results card (full) and
// in the grid tooltip (compact). Mounted by TypedScoreDetails on
// cell.typed.kind === "remote".
import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { SIGNATURE_LABEL, tryLine } from "./remoteModel";

const AUTH_WORDS = {
  hmac: `Signed: ${SIGNATURE_LABEL}`,
  bearer: "Bearer token",
  oauth: "OAuth client credentials",
};

export default function RemoteScoreDetails({ cell, compact = false }) {
  const t = cell?.typed;
  if (!t || t.kind !== "remote") return null;
  const tries = t.tries ?? [];
  const head = t.pending
    ? `Scoring at ${t.host}`
    : t.not_sent
      ? `Not sent to ${t.host}: ${t.not_sent}`
      : t.error
        ? `${t.host} · ${t.error}`
        : `Scored by ${t.host} · ${t.status} · ${tries.length} ${tries.length === 1 ? "try" : "tries"} · ${Math.round(t.latency_ms ?? 0)} ms`;
  if (compact)
    return (
      <Typography
        variant="caption"
        component="div"
        data-testid="remote-score-compact"
      >
        {head}
      </Typography>
    );
  return (
    <Box data-testid="remote-score-details" sx={{ mt: 1 }}>
      <Typography variant="caption" component="div" sx={{ fontWeight: 600 }}>
        Remote scorer · {t.host}
      </Typography>
      <Typography
        variant="caption"
        component="div"
        color={t.error ? "error.main" : "text.secondary"}
      >
        {head}
      </Typography>
      {tries.map((x) => (
        <Typography
          key={x.n}
          variant="caption"
          component="div"
          color="text.secondary"
        >
          {tryLine(x)}
          {x.error && x.status ? ` · ${x.error}` : ""}
        </Typography>
      ))}
      <Typography variant="caption" component="div" color="text.secondary">
        POST {t.url} · delivery {t.delivery_id}
      </Typography>
      {!t.pending && (
        <Typography variant="caption" component="div" color="text.secondary">
          {AUTH_WORDS[t.auth] ?? "No auth"} · billed by your service · 0 Future
          AGI credits
        </Typography>
      )}
    </Box>
  );
}

RemoteScoreDetails.ready = true;

RemoteScoreDetails.propTypes = {
  cell: PropTypes.object,
  compact: PropTypes.bool,
};
