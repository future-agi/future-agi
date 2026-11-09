// A run's name on the leaderboard (R4 lane Rb MUST 7; D26): the run chip,
// the source logo, the agent and version (`Support Agent v3.1 (draft)`),
// the `Latest version` / `Previous version` chip and, when the run is worse
// than the version before it, `▼ 7 tasks worse than v2`. The second line is
// the provenance line (RunProvenance, the logo already sits on line one)
// and, for live local runs, C's RunConnectionStrip.
import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Link, Popover, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import RunChip from "src/sections/common/Runs/RunChip";
import RunProvenance from "src/sections/common/Runs/RunProvenance";
import { provenanceOf } from "src/sections/common/Runs/provenanceModel";
import RunConnectionStrip from "src/sections/common/Connect/RunConnectionStrip";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { Link as RouterLink } from "react-router-dom";
import { madeAfter } from "./leaderboardHeadline";
import { compareUrl, isUnrankedRun } from "./leaderboardModel";
import { fmtPct, fmtPts } from "src/sections/common/format/fmt";
import { plural } from "src/sections/common/vocab";

/**
 * IT-R5 G2 P2: the line states its bases — `vs #1B on the 24 tasks both
 * ran: 66.7% vs 66.7% · #2B on all 30: 65.6%`.
 */
const baseLineText = (b, label, own, noun = "task") => {
  const both = `vs ${b.previous.label} on the ${plural(b.n, noun)} both ran: ${fmtPct(b.cand)} vs ${fmtPct(b.base)}`;
  if (!own || !(own.n > 0) || own.n === b.n) return both;
  return `${both} · ${label} on all ${own.n}: ${fmtPct(own.rate)}`;
};

export function VersionChip({ text, tone = "primary", title }) {
  return (
    <CustomTooltip
      show={Boolean(title)}
      title={title ?? ""}
      size="small"
      placement="top"
      arrow
    >
      <Box
        component="span"
        data-testid="version-chip"
        sx={{
          flexShrink: 0,
          px: 0.75,
          borderRadius: "2px",
          typography: "s3",
          fontWeight: 500,
          lineHeight: "16px",
          whiteSpace: "nowrap",
          bgcolor: (t) =>
            tone === "error"
              ? alpha(t.palette.error.main, 0.12)
              : tone === "primary"
                ? alpha(t.palette.primary.main, 0.12)
                : t.palette.action.hover,
          color:
            tone === "error"
              ? "error.main"
              : tone === "primary"
                ? "primary.main"
                : "text.secondary",
        }}
      >
        {text}
      </Box>
    </CustomTooltip>
  );
}
VersionChip.propTypes = {
  text: PropTypes.string.isRequired,
  tone: PropTypes.string,
  title: PropTypes.string,
};

/**
 * IT-R3 G2 P2: a live local run's second line is one short status —
 * `Running on your machine · Live details` — and the full strip (slots,
 * Stop, Watch) opens in a popover, so a 48 px row never crams it. The
 * progress counter stays in the status column (one counter per run).
 */
export function LiveRunLine({ run }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  // options-truth L3 G3: the run's execution label (`alice@mbp-14`); the
  // terminal's own `This machine` reads as `your machine` in a sentence.
  const exec = run?.config?.execution ?? {};
  const label = exec.label ?? exec.machine_name ?? null;
  const where =
    label && !/^this machine$/i.test(String(label)) ? label : "your machine";
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      sx={{ minWidth: 0 }}
      data-testid="live-run-line"
    >
      <Typography typography="s3" color="text.secondary" noWrap>
        {`Running on ${where}`}
      </Typography>
      <Link
        ref={ref}
        component="button"
        type="button"
        typography="s3"
        underline="hover"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
      >
        Live details
      </Link>
      <Popover
        open={open}
        anchorEl={ref.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { mt: 0.5, p: 1.5, width: 520 } } }}
        onClick={(e) => e.stopPropagation()}
      >
        <RunConnectionStrip run={run} />
      </Popover>
    </Stack>
  );
}
LiveRunLine.propTypes = { run: PropTypes.object.isRequired };

export default function RunIdentity({
  data,
  context,
  title,
  link,
  isBaseline,
  isPinned,
  pills,
  afterLabel = null,
  oneLine = false,
}) {
  const n = context?.numbers;
  const chip = n?.chips?.[data.run_id] ?? null;
  const prevCmp = n?.prevOf?.[data.run_id] ?? null;
  // IT-R4 G2 P5 (rule 30): the pinned baseline names its own trend against
  // its previous version — a line when within noise, a red chip when it
  // really dropped; other runs keep the `▼ … vs #1B` chip.
  const pinnedBase =
    Boolean(context?.pinnedRunId) && data.run_id === context.pinnedRunId;
  const regressed = pinnedBase && prevCmp?.regressed;
  // IT-R5 G2 P2: the line shows for any verdict and is a door to the pair.
  const baseLine = pinnedBase && prevCmp ? prevCmp : null;
  const baseLineHref = baseLine
    ? compareUrl(context?.datasetId, {
        runIds: [baseLine.previous.run_id, data.run_id],
        baselineId: baseLine.previous.run_id,
        candId: data.run_id,
        runs: [baseLine.previous, data],
        from: context?.fromUrl,
        tab: "data",
      })
    : null;
  // Its own rows: tasks with a cell for this run (not tries).
  const ownRows = baseLine
    ? (n?.matrix?.rows ?? []).filter((row) => row.cells?.[data.run_id]).length
    : 0;
  const baseText = baseLine
    ? baseLineText(
        baseLine,
        data.label,
        { n: ownRows, rate: n?.byRun?.[data.run_id]?.rate?.rate },
        n?.noun,
      )
    : "";
  const worse = pinnedBase ? null : n?.worse?.[data.run_id] ?? null;
  // IT-R4 G2 P9: a run someone else started shows who (`asha`), so a
  // fresh stage reads as your team's runs.
  const by = data?.created_by;
  const owner =
    by?.id &&
    context?.viewerId &&
    by.id !== context.viewerId &&
    by.kind !== "bot" &&
    by.kind !== "ci"
      ? {
          name: by.name ?? "a teammate",
          short: String(by.name ?? "teammate")
            .split(" ")[0]
            .toLowerCase(),
        }
      : null;
  // `New`: the run the headline judges, made after the pinned baseline.
  const baseRun = context?.baselineRun;
  const isNew =
    Boolean(context?.newCandId) &&
    data.run_id === context.newCandId &&
    baseRun?.run_id === context?.pinnedRunId &&
    madeAfter(data, baseRun);
  const entryId = provenanceOf(data)?.source?.entryId ?? null;
  // A live local run shows C's strip (`Running on alice@mbp-14 · …`).
  const live =
    data?.config?.execution?.mode === "local" && getRunStatus(data).active;
  return (
    <Stack
      sx={{ minWidth: 0, flex: 1 }}
      justifyContent="center"
      data-testid="run-identity"
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        <Box sx={{ flexShrink: 0, display: "inline-flex" }}>
          <RunChip
            run={data}
            showName={false}
            showStatus={false}
            showSource={false}
            isBaseline={isBaseline}
            isPinned={isPinned}
          />
        </Box>
        {afterLabel}
        {entryId ? <SourceLogo entryId={entryId} size={16} tooltip /> : null}
        {/* W1-1 review should-fix 3: the name keeps room for a readable
            start beside the `2 runs` chip; the chips after it give way. */}
        <Box
          sx={{
            display: "flex",
            minWidth: 72,
            flex: "0 1 auto",
            overflow: "hidden",
          }}
        >
          {link(title)}
        </Box>
        {chip ? (
          <VersionChip
            text={chip}
            title={
              chip === n?.chips?.[n?.pair?.latest?.run_id]
                ? "The newest version of this agent on this dataset"
                : "The version before the latest one"
            }
          />
        ) : null}
        {isUnrankedRun(data) ? (
          <VersionChip
            text="Text only · not ranked"
            tone="neutral"
            title="This run answered in text, with no audio, so it is not ranked against the voice runs: no best, no medal."
          />
        ) : null}
        {owner ? (
          <VersionChip
            text={owner.short}
            tone="neutral"
            title={`Run by ${owner.name}`}
          />
        ) : null}
        {isNew ? (
          <VersionChip
            text="New"
            title={`Made after the baseline ${baseRun.label}: the run this page judges against it`}
          />
        ) : null}
        {regressed ? (
          <VersionChip
            text={`Regressed ${fmtPts(prevCmp.deltaPts)} vs ${prevCmp.previous.label}`}
            tone="error"
            title={`${data.label} vs ${prevCmp.previous.label} (previous ${prevCmp.kind === "run" ? "run" : "version"}): ${fmtPct(prevCmp.cand)} vs ${fmtPct(prevCmp.base)} on ${plural(prevCmp.n, n?.noun ?? "task")}, beyond noise · ${prevCmp.worse} worse`}
          />
        ) : null}
        {worse ? (
          <VersionChip
            text={worse.text}
            tone="error"
            title={`${worse.long ?? worse.text}. Worse = tasks where ${data.label} passed fewer tries than ${worse.previous?.label ?? "the previous version"}.`}
          />
        ) : null}
        {pills}
      </Stack>
      {oneLine ? null : (
        <Box
          sx={{
            minWidth: 0,
            // The logo is already on line one.
            "& [data-run-provenance='line'] > :not(.MuiTypography-root)": {
              display: "none",
            },
            "& [data-run-provenance='line'] .MuiTypography-root": {
              typography: "s3",
            },
          }}
        >
          {baseLine ? (
            <Link
              component={RouterLink}
              to={baseLineHref}
              typography="s3"
              color="text.secondary"
              underline="hover"
              noWrap
              data-row-link
              onClick={(e) => e.stopPropagation()}
              data-testid="baseline-previous-line"
              title={`${baseText}. Open ${baseLine.previous.label} and ${data.label} side by side`}
              sx={{ display: "block", minWidth: 0 }}
            >
              {baseText}
            </Link>
          ) : live ? (
            <LiveRunLine run={data} />
          ) : (
            <RunProvenance run={data} variant="line" />
          )}
        </Box>
      )}
    </Stack>
  );
}

RunIdentity.propTypes = {
  data: PropTypes.object.isRequired,
  context: PropTypes.object,
  title: PropTypes.node,
  link: PropTypes.func.isRequired,
  isBaseline: PropTypes.bool,
  isPinned: PropTypes.bool,
  pills: PropTypes.node,
  // W1-1: beside the run label (the "2 runs" chip), clear of row actions.
  afterLabel: PropTypes.node,
  oneLine: PropTypes.bool,
};
