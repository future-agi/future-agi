import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import { formatLatency } from "../../utils/formatters";
import KeyValueList from "../components/KeyValueList";
import SectionTitle from "../components/SectionTitle";
import { formatCallTime } from "./toolCalls";

// How one call went through the gateway, from the row's `outcomes`
// (agentcc/services/mcp_tool_call_outcomes.py): the gateway's checks and the
// upstream attempt, the approval the call named, and the limit that refused
// it. Read from the list row: a metadata-only call loads nothing more.

const STEP_LABELS = { gateway: "Gateway checks", upstream: "Upstream call" };
const RESULT_LABELS = {
  passed: "Passed",
  denied: "Denied",
  ok: "OK",
  tool_error: "Tool error",
  error: "Error",
  timeout: "Timed out",
  cancelled: "Cancelled",
  not_sent: "Not sent",
};
const LIMIT_LABELS = {
  budget: "Budget",
  rate_limit: "Rate limit",
  credits: "Credits",
  depth: "Call depth",
};

function capitalized(value) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

function attemptText(attempt) {
  const label = RESULT_LABELS[attempt.result] || attempt.result || "Unknown";
  if (attempt.step === "gateway" && attempt.code) {
    return `${label} (${attempt.code})`;
  }
  if (
    attempt.step === "upstream" &&
    attempt.result !== "not_sent" &&
    attempt.latency_ms !== null &&
    attempt.latency_ms !== undefined
  ) {
    return `${label} in ${formatLatency(attempt.latency_ms)}`;
  }
  return label;
}

function Section({ title, children }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <SectionTitle title={title} component="h3" />
      {children}
    </Box>
  );
}

Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

export default function CallOutcomes({ outcomes }) {
  if (!outcomes || typeof outcomes !== "object") return null;
  const attempts = Array.isArray(outcomes.attempts) ? outcomes.attempts : [];
  const approval = outcomes.approval;
  const limits = outcomes.limits || {};
  return (
    <>
      {attempts.length > 0 && (
        <Section title="Attempts">
          <KeyValueList
            rows={attempts.map((attempt) => ({
              label: STEP_LABELS[attempt.step] || attempt.step,
              value: attemptText(attempt),
            }))}
          />
        </Section>
      )}

      {approval && (
        <Section title="Approval">
          <KeyValueList
            rows={[
              { label: "Approval", value: approval.approval_id, mono: true },
              {
                label: "Status",
                value: approval.status
                  ? capitalized(approval.status)
                  : "Not found in this organization",
              },
              {
                label: "Decided",
                value: approval.decided_at
                  ? formatCallTime(approval.decided_at)
                  : undefined,
              },
            ]}
          />
        </Section>
      )}

      <Section title="Limits">
        <KeyValueList
          rows={[
            {
              label: "Stopped by",
              value: limits.stopped_by
                ? LIMIT_LABELS[limits.stopped_by] || limits.stopped_by
                : "No limit stopped this call",
            },
            {
              label: "Limit code",
              value: limits.code || undefined,
              mono: true,
            },
            { label: "Budget scope", value: limits.budget_scope || undefined },
          ]}
        />
      </Section>
    </>
  );
}

CallOutcomes.propTypes = {
  outcomes: PropTypes.shape({
    attempts: PropTypes.arrayOf(PropTypes.object),
    approval: PropTypes.object,
    limits: PropTypes.object,
  }),
};
