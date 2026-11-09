/* eslint-disable react-refresh/only-export-components */
import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Collapse from "@mui/material/Collapse";
import Typography from "@mui/material/Typography";

import { formatDate, shortDigest } from "../lib/format";
import { checkTone } from "../lib/status";

import HubIcon from "./HubIcon";
import { HubSection } from "./HubStates";
import ReviewMeans from "./ReviewMeans";
import { toneSx } from "./ToneChip";

const STEP_LABEL = {
  unpack: "Unpack",
  manifest: "Manifest and format",
  licence: "Licence",
  secrets: "Secrets",
  dependencies: "Dependencies",
  "static-scan": "Static scan",
  "sandbox-run": "Sandbox run",
  capabilities: "Capabilities",
  diff: "Changes",
  similarity: "Similarity",
};

export const STEP_ORDER = Object.keys(STEP_LABEL);

export function orderedSteps(steps) {
  const rank = (id) =>
    STEP_ORDER.includes(id) ? STEP_ORDER.indexOf(id) : STEP_ORDER.length;
  return Object.entries(steps || {}).sort(([a], [b]) => rank(a) - rank(b));
}

export function stepLabel(id) {
  return STEP_LABEL[id] || id;
}

export function StepStatus({ status, reviewed }) {
  const tone = checkTone(status);
  const text =
    {
      pass: "passed",
      flag: reviewed ? "flagged, reviewed" : "flagged",
      fail: "failed",
    }[status] ||
    status ||
    "—";
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        ...toneSx(tone),
      }}
    >
      <HubIcon
        name={{ pass: "check", fail: "close", flag: "flag" }[status] || "dots"}
        size={12}
      />
      <Typography
        component="span"
        typography="s3"
        sx={{ color: "text.secondary" }}
      >
        {text}
      </Typography>
    </Box>
  );
}

StepStatus.propTypes = { status: PropTypes.string, reviewed: PropTypes.bool };

// Evidence (SPEC §6.7, §7.2): who approved it (as "Future AGI", never the
// maintainer's name), what was checked, and what review does not mean.
export default function EvidencePanel({ version, tier }) {
  const [open, setOpen] = useState(false);
  if (!version) return null;
  const summary = version.checkSummary || {};
  const steps = summary.steps || {};
  const live =
    tier === "official"
      ? (version.manifest?.models?.live || []).filter((m) => m?.checked)
      : [];
  return (
    <HubSection title="Evidence" testId="hub-evidence">
      <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start" }}>
        <Box sx={{ ...toneSx(checkTone("pass")), mt: 0.25 }}>
          <HubIcon name="shieldCheck" size={16} />
        </Box>
        <Typography
          typography="s2"
          color="text.primary"
          data-testid="hub-evidence-reviewed"
        >
          {`Reviewed by Future AGI · ${formatDate(version.approvedAt)} · version ${version.version} · digest ${shortDigest(version.digest)}`}
          {version.sourceCommit
            ? ` · from commit ${version.sourceCommit.slice(0, 7)}`
            : ""}
        </Typography>
      </Box>
      <Typography typography="s2" color="text.secondary">
        {`Checked automatically · sandbox run, network ${summary.network === "none" || !summary.network ? "off" : summary.network}${summary.omegaCommit ? ` · Omega ${summary.omegaCommit.slice(0, 7)}` : ""}`}
      </Typography>
      <Box
        component="ul"
        sx={{ m: 0, p: 0, listStyle: "none", display: "grid", gap: 0.5 }}
      >
        {orderedSteps(steps).map(([id, status]) => (
          <Box
            component="li"
            key={id}
            sx={{ display: "flex", justifyContent: "space-between", gap: 1 }}
          >
            <Typography typography="s2" color="text.secondary">
              {stepLabel(id)}
            </Typography>
            <StepStatus status={status} reviewed />
          </Box>
        ))}
      </Box>
      {summary.flagsReviewed > 0 && (
        <Typography typography="s2" color="text.secondary">
          {`${summary.flagsReviewed} flagged call${summary.flagsReviewed === 1 ? "" : "s"} reviewed by a maintainer`}
        </Typography>
      )}
      {live.map((model) => (
        <Typography key={model.model} typography="s2" color="text.secondary">
          {`Checked live with ${model.model} on ${formatDate(model.checked)}`}
        </Typography>
      ))}
      {version.rulesVersion && (
        <Typography typography="s3" color="text.tertiary">
          {`Approved under Hub rules version ${version.rulesVersion}`}
        </Typography>
      )}
      <Box>
        <Button
          size="small"
          color="inherit"
          onClick={() => setOpen((v) => !v)}
          endIcon={
            <HubIcon
              name="chevronDown"
              size={14}
              sx={{ transform: open ? "rotate(180deg)" : "none" }}
            />
          }
          sx={{ px: 0, color: "text.secondary" }}
          aria-expanded={open}
        >
          What does this mean?
        </Button>
        <Collapse in={open} unmountOnExit>
          <ReviewMeans dense />
        </Collapse>
      </Box>
    </HubSection>
  );
}

EvidencePanel.propTypes = {
  version: PropTypes.object,
  tier: PropTypes.string,
};
