import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Collapse from "@mui/material/Collapse";
import Typography from "@mui/material/Typography";

import { useHubRules } from "../api/queries";
import { formatBytes, formatDate, shortDigest } from "../lib/format";

import { CopyButton } from "./CopyCommand";
import DateStamp from "./DateStamp";
import { StepStatus, orderedSteps, stepLabel } from "./EvidencePanel";
import HubIcon from "./HubIcon";
import HubPanel from "./HubPanel";
import ReviewMeans from "./ReviewMeans";

// Code-like values (a version, a commit, a digest) in the theme's mono face
// at the size of the fact list around them.
const CODE = { typography: "code_sm" };

// A digest's copy button: a 24 px target (WCAG 2.2 AA asks for 24), pulled
// back by its own margin so a digest row keeps its line height.
const COPY_DIGEST_SX = { p: 0.75, my: -0.5, color: "text.secondary" };

// What each digest covers, on hover over its row's label. The files digest
// is taken over the approved files; the archive digest is the sha256 of the
// .tar.gz the download serves, the value its X-Omega-Hub-Bundle-Sha256
// header carries.
const FILES_DIGEST_TITLE =
  "The digest of the approved files, not of the downloaded archive.";
const ARCHIVE_DIGEST_TITLE = "The sha256 of the .tar.gz you download.";

// A toggle's chevron sits the theme's icon gap (6 px) after its words, never
// touching them, and nothing trails it.
const END_ICON_SX = { "& .MuiButton-endIcon": { ml: 0.75, mr: 0 } };

function Row({ label, title, children }) {
  return (
    <>
      <Typography
        component="dt"
        typography="s2"
        color="text.secondary"
        title={title}
      >
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
  title: PropTypes.string,
  children: PropTypes.node,
};

// A digest in its short form, the whole value on hover, and a button that
// copies the whole value.
function Digest({ value, copyLabel }) {
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.25 }}
    >
      <Box component="span" title={value} sx={CODE}>
        {shortDigest(value, 12)}
      </Box>
      <CopyButton
        text={value}
        label={copyLabel}
        size={12}
        sx={COPY_DIGEST_SX}
      />
    </Box>
  );
}

Digest.propTypes = {
  value: PropTypes.string.isRequired,
  copyLabel: PropTypes.string.isRequired,
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
        ...END_ICON_SX,
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
// from only when the version records one, its two digests and its size, the
// rules it was approved under, what the checker ran, each check's result and
// what review means. Nothing here is drawn without the field behind it.
// The files digest is taken over the approved files; the archive digest is
// the sha256 of the .tar.gz the download serves, so a download is checked
// against that one. Each row's label says on hover which it is, and each
// digest is copied whole from beside its short form. Each check's result is
// listed as the page loads, as the review evidence always was: in the side
// column one check to a row, its result at the row's end; on a card with the
// page's width (`spread`) as many to a row as fit, each result right after
// its own name and a wide gutter before the next check, so no result reads
// as the next one's. Hide the checks folds the list for a visitor who wants
// it shorter.
export default function EntryProvenance({
  entry,
  version,
  tier,
  spread = false,
}) {
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
          <Row label="Files digest" title={FILES_DIGEST_TITLE}>
            <Digest value={version.digest} copyLabel="Copy the whole digest" />
          </Row>
        )}
        {version.bundleSha256 && (
          <Row label="Archive digest" title={ARCHIVE_DIGEST_TITLE}>
            <Digest
              value={version.bundleSha256}
              copyLabel="Copy the archive digest"
            />
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
                  gridTemplateColumns: spread
                    ? "repeat(auto-fill, minmax(200px, 1fr))"
                    : "minmax(0, 1fr)",
                  columnGap: 6,
                  rowGap: 0.5,
                }}
              >
                {steps.map(([id, status]) => (
                  <Box
                    component="li"
                    key={id}
                    sx={{
                      display: "flex",
                      justifyContent: spread ? "flex-start" : "space-between",
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
  spread: PropTypes.bool,
};
