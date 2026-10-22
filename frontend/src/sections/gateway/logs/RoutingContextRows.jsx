import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography } from "@mui/material";
import { formatLatency } from "../utils/formatters";
import { asList, asNumber, asText } from "./autoDecision";
import DetailRow from "./DetailRow";
import { isKnownValue } from "./explorerLabels";

// Rows for the context a routing decision was made in: the agent's step, the
// session affinity and the caller's constraints (auto-decision-v2), and the
// policy version and catalog snapshot (the log's routing block). Each row
// renders only when its value is present.

const AFFINITY = {
  hit: "Hit (reused the pinned model)",
  new: "New pin",
  off: "Off",
};

const BROKEN = "broken:";

function affinityLabel(value) {
  if (value.startsWith(BROKEN)) return `Broken: ${value.slice(BROKEN.length)}`;
  return isKnownValue(AFFINITY, value) ? AFFINITY[value] : value;
}

function describe(value) {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value && typeof value === "object") {
    return Object.entries(value)
      .map(([key, v]) => `${key}=${v}`)
      .join(", ");
  }
  return String(value);
}

const isEmpty = (value) =>
  value == null || value === "" || (Array.isArray(value) && value.length === 0);

// The caller's own cap is shown as it was sent, not rounded.
const CONSTRAINT_LABELS = {
  call_cap_usd: (v) =>
    asNumber(v) == null ? null : `Call cap $${asNumber(v)}`,
  deadline_ms: (v) =>
    asNumber(v) == null ? null : `Deadline ${formatLatency(asNumber(v))}`,
  require: (v) =>
    asList(v).length ? `Requires ${asList(v).join(", ")}` : null,
  route_hint: (v) => `Route hint ${describe(v)}`,
  data_policy: (v) => `Data policy ${describe(v)}`,
  request_provider: (v) => `Provider ${describe(v)}`,
};

function constraintLabels(constraints) {
  return Object.entries(constraints)
    .filter(([, value]) => !isEmpty(value))
    .map(([key, value]) =>
      isKnownValue(CONSTRAINT_LABELS, key)
        ? CONSTRAINT_LABELS[key](value)
        : `${key} ${describe(value)}`,
    )
    .filter(Boolean);
}

export function AutoContextRows({ log, decision }) {
  const step = asText(log.agent?.step);
  const stepSource = asText(log.agent?.step_source);
  const affinity = decision?.affinity;
  const constraints = decision?.constraints
    ? constraintLabels(decision.constraints)
    : [];

  return (
    <>
      {step && (
        <DetailRow label="Step">
          {step}
          {stepSource ? ` (${stepSource})` : ""}
        </DetailRow>
      )}
      {affinity && (
        <DetailRow label="Affinity">{affinityLabel(affinity)}</DetailRow>
      )}
      {constraints.length > 0 && (
        <Typography variant="body2" component="div">
          <Stack
            direction="row"
            spacing={0.5}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
          >
            <strong>Constraints:</strong>
            {constraints.map((label) => (
              <Chip key={label} label={label} size="small" variant="outlined" />
            ))}
          </Stack>
        </Typography>
      )}
    </>
  );
}

AutoContextRows.propTypes = {
  log: PropTypes.object.isRequired,
  decision: PropTypes.object,
};

export function PolicyRows({ routing }) {
  const version = asText(routing?.policy_version);
  const variant = asText(routing?.policy_variant);
  const snapshot = asText(routing?.catalog_snapshot_id);

  return (
    <>
      {(version || variant) && (
        <DetailRow label="Policy">
          {[version, variant && `(${variant})`].filter(Boolean).join(" ")}
        </DetailRow>
      )}
      {snapshot && (
        <DetailRow label="Catalog snapshot">
          <Box component="span" sx={{ fontFamily: "monospace" }}>
            {snapshot}
          </Box>
        </DetailRow>
      )}
    </>
  );
}

PolicyRows.propTypes = { routing: PropTypes.object };
