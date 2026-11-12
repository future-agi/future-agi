import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Collapse from "@mui/material/Collapse";
import Typography from "@mui/material/Typography";

import { useHubRules } from "../api/queries";
import { formatBytes, formatDate, shortDigest } from "../lib/format";

import DateStamp from "./DateStamp";
import { StepStatus, orderedSteps, stepLabel } from "./EvidencePanel";
import HubIcon from "./HubIcon";
import HubPanel from "./HubPanel";
import ReviewMeans from "./ReviewMeans";

// Code-like values (a version, a commit, a digest) in the theme's mono face
// at the size of the fact list around them.
const CODE = { typography: "code_sm" };

function Row({ label, children }) {
  return (
    <>
      <Typography component="dt" typography="s2" color="text.secondary">
        {label}
      </Typography>
      <Typography
        component="dd"
        typography="s2"
        color="text.primary"
        sx={{ m: 0, minWidth: 0, overflowWrap: "anywhere" }}
      >
        {children}
      </Typography>
    </>
  );
}

Row.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function Toggle({ open, onClick, controls, children }) {
  return (
    <Button
      size="small"
      color="inherit"
      onClick={onClick}
      aria-expanded={open}
      aria-controls={open ? controls : undefined}
      endIcon={
        <HubIcon
          name="chevronDown"
          size={14}
          sx={{ transform: open ? "rotate(180deg)" : "none" }}
        />
      }
      sx={{
        px: 0,
        minWidth: 0,
        alignSelf: "flex-start",
        color: "text.secondary",
        "&:hover": { color: "text.primary", bgcolor: "transparent" },
      }}
    >
      {children}
    </Button>
  );
}

Toggle.propTypes = {
  open: PropTypes.bool.isRequired,
  onClick: PropTypes.func.isRequired,
  controls: PropTypes.string.isRequired,
  children: PropTypes.node,
};

// Provenance (SPEC §6.7, §7.2), the last fact card of the side column, like
// the metadata column of a package page: who approved this version and when
// (always a Future AGI maintainer, never by name), the commit it was built
// from only when the version records one, its bundle digest and size, the
// rules it was approved under, what the checker ran, each check's result and
// what review means. Nothing here is drawn without the field behind it.
// Each check's result is listed as the page loads, as the review evidence
// always was; Hide the checks folds the list for a visitor who wants it
// shorter.
export default function EntryProvenance({ entry, version, tier }) {
  const [checksOpen, setChecksOpen] = useState(true);
  const [meansOpen, setMeansOpen] = useState(false);
  const checksId = useId();
  const meansId = useId();
  const { data: rules } = useHubRules();
  if (!version) return null;
  const summary = version.checkSummary || {};
  const steps = orderedSteps(summary.steps);
  const network =
    summary.network === "none" || !summary.network ? "off" : summary.network;
  const keylessPassed = summary.steps?.["sandbox-run"] === "pass";
  const live =
    tier === "official"
      ? (version.manifest?.models?.live || []).filter((model) => model?.checked)
      : [];
  const fileCount = Array.isArray(version.files) ? version.files.length : 0;
  const size = [
    version.bundleBytes ? formatBytes(version.bundleBytes) : null,
    fileCount ? `${fileCount} file${fileCount === 1 ? "" : "s"}` : null,
  ].filter(Boolean);
  const firstListed =
    entry?.firstPublishedAt &&
    formatDate(entry.firstPublishedAt) !== formatDate(version.approvedAt)
      ? entry.firstPublishedAt
      : null;
  const hasMeans = Array.isArray(rules?.reviewMeans)
    ? rules.reviewMeans.length > 0
    : Boolean(rules?.reviewMeans);
  const ran = keylessPassed
    ? `Its keyless test passed in the checker with the network ${network}`
    : `Checked automatically with the network ${network}`;
  return (
    <HubPanel title="Provenance" testId="hub-provenance" dense>
      <Typography
        typography="s1_2"
        color="text.primary"
        data-testid="hub-provenance-approved"
      >
        {"Checked and approved on "}
        <DateStamp iso={version.approvedAt} withAgo />
        {" by a Future AGI maintainer"}
      </Typography>
      <Box
        component="dl"
        sx={{
          m: 0,
          display: "grid",
          gridTemplateColumns: "88px minmax(0, 1fr)",
          rowGap: 0.75,
          columnGap: 1.5,
        }}
      >
        <Row label="Version">
          <Box component="span" sx={CODE}>
            {version.version}
          </Box>
        </Row>
        {version.manifest?.license && (
          <Row label="Licence">{version.manifest.license}</Row>
        )}
        {firstListed && (
          <Row label="First listed">
            <DateStamp iso={firstListed} />
          </Row>
        )}
        {version.sourceCommit && (
          <Row label="Source">
            <Box component="span" title={version.sourceCommit}>
              {"From commit "}
              <Box component="code" sx={CODE}>
                {version.sourceCommit.slice(0, 7)}
              </Box>
            </Box>
          </Row>
        )}
        {version.digest && (
          <Row label="Bundle">
            <Box component="span" title={version.digest} sx={CODE}>
              {shortDigest(version.digest, 12)}
            </Box>
          </Row>
        )}
        {size.length > 0 && <Row label="Size">{size.join(", ")}</Row>}
        {version.rulesVersion && (
          <Row label="Rules">{`Hub rules version ${version.rulesVersion}`}</Row>
        )}
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 0.5,
          pt: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography typography="s2" color="text.secondary">
          {`${ran}${summary.omegaCommit ? ` · Omega ${summary.omegaCommit.slice(0, 7)}` : ""}.`}
        </Typography>
        {summary.flagsReviewed > 0 && (
          <Typography typography="s2" color="text.secondary">
            {`${summary.flagsReviewed} flagged call${summary.flagsReviewed === 1 ? "" : "s"} reviewed by a maintainer.`}
          </Typography>
        )}
        {live.map((model) => (
          <Typography key={model.model} typography="s2" color="text.secondary">
            {`Checked live with ${model.model} on ${formatDate(model.checked)}.`}
          </Typography>
        ))}
        {steps.length > 0 && (
          <>
            <Toggle
              open={checksOpen}
              onClick={() => setChecksOpen((open) => !open)}
              controls={checksId}
            >
              {checksOpen
                ? "Hide the checks"
                : `Show the ${steps.length} checks`}
            </Toggle>
            <Collapse in={checksOpen} unmountOnExit>
              <Box
                component="ul"
                id={checksId}
                aria-label="Checks"
                sx={{
                  m: 0,
                  p: 0,
                  listStyle: "none",
                  display: "grid",
                  gap: 0.5,
                }}
              >
                {steps.map(([id, status]) => (
                  <Box
                    component="li"
                    key={id}
                    sx={{
                      display: "flex",
                      justifyContent: "space-between",
                      gap: 1,
                    }}
                  >
                    <Typography typography="s2" color="text.secondary">
                      {stepLabel(id)}
                    </Typography>
                    <StepStatus status={status} reviewed />
                  </Box>
                ))}
              </Box>
            </Collapse>
          </>
        )}
        {hasMeans && (
          <>
            <Toggle
              open={meansOpen}
              onClick={() => setMeansOpen((open) => !open)}
              controls={meansId}
            >
              What review means
            </Toggle>
            <Collapse in={meansOpen} unmountOnExit>
              <Box id={meansId}>
                <ReviewMeans rules={rules} dense />
              </Box>
            </Collapse>
          </>
        )}
      </Box>
    </HubPanel>
  );
}

EntryProvenance.propTypes = {
  entry: PropTypes.object,
  version: PropTypes.object,
  tier: PropTypes.string,
};
