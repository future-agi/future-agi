/* eslint-disable react-refresh/only-export-components */
import React, { useId } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { allFindings, formatDuration } from "../lib/report";
import { checkTone } from "../lib/status";

import { StepStatus, stepLabel } from "./EvidencePanel";
import HubIcon from "./HubIcon";
import { toneSx } from "./ToneChip";

const VISUALLY_HIDDEN = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

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

const FAILED = ["fail", "block"];
const STEP_ICON = {
  pass: "check",
  fail: "close",
  block: "close",
  flag: "flag",
};
const STEP_WORD = { pass: "passed", fail: "failed", block: "failed" };
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
// The sandbox run's network in words: "none" is off; any other setting is on,
// named the way the checker names it ("on (allowlist)").
const networkWord = (network) =>
  !network || network === "none" ? "off" : `on (${network})`;

function stepFlags(step) {
  return (step.findings || []).filter((f) => f.severity === "flag").length;
}

function stepDetail(step) {
  const flags = stepFlags(step);
  if (step.status === "flag" && flags) return plural(flags, "flag");
  if (step.ms === undefined || step.ms === null) return "";
  if (step.id === "sandbox-run" && step.network)
    return `${formatDuration(step.ms)}, network ${networkWord(step.network)}`;
  return formatDuration(step.ms);
}

// The one-line verdict of a check report: passed or failed, how many flags in
// how many rule groups, and how long the keyless sandbox run took.
export function summaryLine(report, { flagCount, groupCount, answerable }) {
  const steps = report?.steps || [];
  if (!steps.length) return ["This submission has no check report.", ""];
  const failed = steps.filter((step) => FAILED.includes(step.status));
  if (failed.length) {
    const names = failed.map((step) => stepLabel(step.id)).join(", ");
    return ["Checks failed.", ` Did not pass: ${names}.`];
  }
  let rest = "";
  if (flagCount && answerable)
    rest += ` ${plural(flagCount, "flag")} in ${plural(groupCount, "group")} ${flagCount === 1 ? "needs" : "need"} your answer.`;
  else if (flagCount)
    rest += ` ${plural(flagCount, "flag")} in ${plural(groupCount, "group")}.`;
  else rest += " Nothing was flagged.";
  const run = steps.find((step) => step.id === "sandbox-run");
  if (run?.status === "pass" && run.ms !== undefined && run.ms !== null)
    rest += ` Its keyless sandbox run passed in ${formatDuration(run.ms)} with the network ${networkWord(run.network)}.`;
  return ["Checks passed.", rest];
}

function StepPill({ step }) {
  const detail = stepDetail(step);
  const word = STEP_WORD[step.status];
  const flagged = step.status === "flag";
  const failed = FAILED.includes(step.status);
  return (
    <Box
      component="li"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        px: 1,
        py: 0.25,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: flagged
          ? "status.warning.border"
          : failed
            ? "status.danger.border"
            : "divider",
        bgcolor: flagged ? "status.warning.bg" : "background.paper",
      }}
    >
      <Box
        component="span"
        sx={{ ...toneSx(checkTone(step.status)), display: "inline-flex" }}
      >
        <HubIcon name={STEP_ICON[step.status] || "dots"} size={12} />
      </Box>
      <Typography component="span" typography="s2" color="text.primary">
        {stepLabel(step.id)}
      </Typography>
      {detail && (
        <Typography component="span" typography="s2" color="text.secondary">
          {detail}
        </Typography>
      )}
      {word && (
        <Box component="span" sx={VISUALLY_HIDDEN}>
          {`, ${word}`}
        </Box>
      )}
    </Box>
  );
}

StepPill.propTypes = { step: PropTypes.object.isRequired };

// The check report as the review page opens with it: the verdict line, each
// step as a small pill, and an optional action row (the recheck).
export function CheckSummary({
  report,
  flagCount,
  groupCount,
  answerable = false,
  action,
}) {
  const titleId = useId();
  const steps = report?.steps || [];
  const flags = allFindings(report).filter((f) => f.severity === "flag");
  const [headline, rest] = summaryLine(report, {
    flagCount: flagCount ?? flags.length,
    groupCount: groupCount ?? new Set(flags.map((f) => f.rule || f.code)).size,
    answerable,
  });
  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      data-testid="hub-check-summary"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        minWidth: 0,
      }}
    >
      <Typography
        component="h2"
        id={titleId}
        typography="m3"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
      >
        Check report
      </Typography>
      <Typography typography="s1" color="text.primary">
        <Box component="strong" sx={{ fontWeight: "fontWeightSemiBold" }}>
          {headline}
        </Box>
        {rest}
      </Typography>
      {steps.length > 0 && (
        <Box
          component="ul"
          aria-label="Check steps"
          sx={{
            m: 0,
            p: 0,
            listStyle: "none",
            display: "flex",
            flexWrap: "wrap",
            gap: 0.75,
          }}
        >
          {steps.map((step) => (
            <StepPill key={step.id} step={step} />
          ))}
        </Box>
      )}
      {action}
    </Box>
  );
}

CheckSummary.propTypes = {
  report: PropTypes.object,
  flagCount: PropTypes.number,
  groupCount: PropTypes.number,
  answerable: PropTypes.bool,
  action: PropTypes.node,
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
        This submission has no check report.
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
