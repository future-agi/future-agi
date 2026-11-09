/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { formatDuration } from "../lib/report";
import { checkTone } from "../lib/status";

import { StepStatus, stepLabel } from "./EvidencePanel";
import HubIcon from "./HubIcon";
import { toneSx } from "./ToneChip";

function Finding({ finding, acknowledged, onToggle }) {
  const flag = finding.severity === "flag";
  return (
    <Box
      component="li"
      data-testid="hub-finding"
      data-severity={finding.severity}
      sx={{
        display: "flex",
        gap: 1,
        alignItems: "flex-start",
        py: 0.75,
        px: 1,
        borderRadius: 0.75,
        bgcolor: "background.neutral",
      }}
    >
      {onToggle && flag ? (
        <Box
          component="input"
          type="checkbox"
          checked={Boolean(acknowledged)}
          onChange={() => onToggle(finding.id)}
          aria-label={`Acknowledge ${finding.id}`}
          data-testid="hub-flag-ack"
          data-flag={finding.id}
          sx={{ mt: 0.25, accentColor: "currentColor" }}
        />
      ) : (
        <Box
          sx={{
            ...toneSx(checkTone(finding.severity)),
            display: "flex",
            mt: 0.25,
          }}
        >
          <HubIcon name={flag ? "flag" : "alert"} size={13} />
        </Box>
      )}
      <Box sx={{ minWidth: 0 }}>
        <Typography
          typography="s2"
          color="text.primary"
          sx={{ overflowWrap: "anywhere" }}
        >
          {finding.message}
        </Typography>
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
        >
          {[
            finding.rule || finding.code,
            finding.file &&
              `${finding.file}${finding.line ? `:${finding.line}` : ""}`,
            finding.id,
          ]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
        {finding.fix && (
          <Typography typography="s3" color="text.secondary">
            {`Fix: ${finding.fix}`}
          </Typography>
        )}
      </Box>
    </Box>
  );
}

Finding.propTypes = {
  finding: PropTypes.object,
  acknowledged: PropTypes.bool,
  onToggle: PropTypes.func,
};

// The check report (SPEC §4.5): each step with its status and findings. For
// a reviewer, each flag carries an acknowledge box (SPEC §5.3).
export default function CheckReport({
  report,
  acknowledged,
  onToggleFlag,
  onlyProblems,
}) {
  const steps = report?.steps || [];
  if (!steps.length) {
    return (
      <Typography typography="s2" color="text.secondary">
        No check report yet.
      </Typography>
    );
  }
  return (
    <Box
      component="ol"
      data-testid="hub-check-report"
      sx={{ m: 0, p: 0, listStyle: "none", display: "grid", gap: 1 }}
    >
      {steps
        .filter((step) => !onlyProblems || step.status !== "pass")
        .map((step) => (
          <Box
            component="li"
            key={step.id}
            data-step={step.id}
            sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}
          >
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                gap: 1,
                alignItems: "center",
              }}
            >
              <Typography typography="s2" fontWeight={600}>
                {stepLabel(step.id)}
              </Typography>
              <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                {step.ms !== undefined && (
                  <Typography typography="s3" color="text.tertiary">
                    {formatDuration(step.ms)}
                  </Typography>
                )}
                <StepStatus status={step.status} />
              </Box>
            </Box>
            {step.id === "sandbox-run" && step.network && (
              <Typography typography="s3" color="text.secondary">
                {`Network: ${step.network}`}
              </Typography>
            )}
            {(step.findings || []).length > 0 && (
              <Box
                component="ul"
                sx={{
                  m: 0,
                  p: 0,
                  listStyle: "none",
                  display: "grid",
                  gap: 0.5,
                }}
              >
                {step.findings.map((finding, index) => (
                  <Finding
                    key={finding.id || `${step.id}-${index}`}
                    finding={finding}
                    acknowledged={acknowledged?.includes(finding.id)}
                    onToggle={onToggleFlag}
                  />
                ))}
              </Box>
            )}
          </Box>
        ))}
    </Box>
  );
}

CheckReport.propTypes = {
  report: PropTypes.object,
  acknowledged: PropTypes.array,
  onToggleFlag: PropTypes.func,
  onlyProblems: PropTypes.bool,
};
