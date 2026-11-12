import React, { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Checkbox, Link, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { Link as RouterLink } from "react-router-dom";
import RunIdentity from "./RunIdentity";
import RunsCountChip from "src/sections/common/Runs/RunsCountChip";
import { runTitleOf } from "./leaderboardVersions";
import { useCell } from "./cellContext";
import CustomTooltip from "src/components/tooltip";
import { fmtCount } from "src/sections/common/format/fmt";
import { provenanceOf } from "src/sections/common/Runs/provenanceModel";
import {
  kindLabel,
  outputModalityOf,
  runKindOf,
} from "src/sections/common/Runs/runKind";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import {
  distinctConfigLine,
  experimentUrl,
  experimentRunsOf,
  runConfigLine,
} from "./leaderboardModel";
import ExperimentTagChips from "src/sections/common/Tags/ExperimentTagChips";
import OtherExperimentRunLink from "../ExperimentTabClassic/OtherExperimentRunLink";

const Pill = ({ children, tone = "default", title }) => (
  <CustomTooltip
    show={!!title}
    title={title}
    size="small"
    placement="top"
    arrow
  >
    <Box
      component="span"
      sx={{
        flexShrink: 0,
        px: 0.75,
        borderRadius: "2px",
        typography: "s3",
        fontWeight: 500,
        lineHeight: "16px",
        bgcolor: (t) =>
          tone === "primary"
            ? alpha(t.palette.primary.main, 0.12)
            : t.palette.action.hover,
        color: tone === "primary" ? "primary.main" : "text.secondary",
      }}
    >
      {children}
    </Box>
  </CustomTooltip>
);

Pill.propTypes = {
  children: PropTypes.node,
  tone: PropTypes.oneOf(["default", "primary"]),
  title: PropTypes.string,
};

/**
 * EL#40: the search match inside a name, marked (first match, any case).
 */
export const Highlight = ({ text, q }) => {
  const value = String(text ?? "");
  const needle = String(q ?? "").trim();
  const at = needle ? value.toLowerCase().indexOf(needle.toLowerCase()) : -1;
  if (at < 0) return value;
  return (
    <>
      {value.slice(0, at)}
      <Box
        component="mark"
        sx={(theme) => ({
          color: "inherit",
          bgcolor: alpha(theme.palette.warning.main, 0.28),
          borderRadius: "2px",
          px: "1px",
        })}
      >
        {value.slice(at, at + needle.length)}
      </Box>
      {value.slice(at + needle.length)}
    </>
  );
};

Highlight.propTypes = { text: PropTypes.string, q: PropTypes.string };

/** EL#13: a name link; `title` = the full text, shown when it truncates. */
const NameLink = ({ to, children, bold = true, title }) => {
  const link = (
    <Link
      component={RouterLink}
      to={to}
      underline="hover"
      color="text.primary"
      typography="s1"
      fontWeight={bold ? "fontWeightMedium" : "fontWeightRegular"}
      noWrap
      data-row-link
      onClick={(e) => e.stopPropagation()}
      sx={{ minWidth: 0 }}
    >
      {children}
    </Link>
  );
  if (!title) return link;
  return (
    <CustomTooltip
      show
      title={title}
      size="small"
      placement="top"
      arrow
      enterDelay={400}
    >
      {link}
    </CustomTooltip>
  );
};

NameLink.propTypes = {
  to: PropTypes.string.isRequired,
  children: PropTypes.node,
  bold: PropTypes.bool,
  title: PropTypes.string,
};

/**
 * IT-R5 G2 P7: how many chips of a one-line row fit, so the row ends in
 * `+2` right after the last whole chip instead of dropping chips silently.
 * `measureRef` holds every chip once (hidden); `outerRef` is the room.
 * Re-measured when the column is resized.
 */
const CHIP_GAP_PX = 4;
const MORE_CHIP_PX = 30;
function useFittingChips(outerRef, measureRef, deps) {
  const [fit, setFit] = useState(Infinity);
  useLayoutEffect(() => {
    const outer = outerRef.current;
    const box = measureRef.current;
    if (!outer || !box) return undefined;
    const measure = () => {
      const widths = [...box.children].map((e) => e.offsetWidth);
      const avail = outer.clientWidth;
      if (!avail || widths.length < 2) return;
      const chips = widths.slice(1);
      let used = widths[0];
      let k = 0;
      for (let i = 0; i < chips.length; i += 1) {
        const rest = i < chips.length - 1 ? CHIP_GAP_PX + MORE_CHIP_PX : 0;
        if (used + CHIP_GAP_PX + chips[i] + rest > avail) break;
        used += CHIP_GAP_PX + chips[i];
        k += 1;
      }
      setFit(k);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(outer);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return fit;
}

const chipSx = {
  display: "inline-flex",
  alignItems: "center",
  gap: 0.5,
  flexShrink: 0,
  height: 18,
  px: 0.75,
  borderRadius: "2px",
  typography: "s3",
  lineHeight: "18px",
  whiteSpace: "nowrap",
  bgcolor: (t) => t.palette.action.hover,
  color: "text.secondary",
};

/** Group row of an experiment: `#4` · name · description · `3 runs`. */
function ExperimentGroupName({ node, context }) {
  const runs = (node.allLeafChildren ?? []).map((n) => n.data).filter(Boolean);
  const first = runs[0];
  const sources = experimentSources(runs);
  // Chips when the runs differ in kind, or the output is not the dataset's
  // usual one (a spoken prompt, a voice agent run on text).
  const showSources = sources.length > 1 || sources.some((x) => x.special);
  const chipsRef = useRef(null);
  const measureRef = useRef(null);
  const sourceKey = sources.map((x) => x.name).join("|");
  const fit = useFittingChips(chipsRef, measureRef, [sourceKey, showSources]);
  const shownSources = sources.slice(0, fit);
  const hiddenCount = sources.length - shownSources.length;
  if (!first) return null;
  const isLatest =
    !!context.latestExperimentId &&
    first.experiment_id === context.latestExperimentId;
  const hiddenNames = hiddenCount
    ? sources.slice(shownSources.length).map((x) => x.name)
    : [];
  return (
    <Stack
      sx={{ minWidth: 0, py: 0.5, flex: "1 1 auto", width: "100%" }}
      justifyContent="center"
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        {/* The Results run list has no experiment number: the name alone
            labels the group then, never a bare `#`. */}
        {first.experiment_ordinal != null ? (
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            color="text.secondary"
            sx={{ fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
          >
            #{first.experiment_ordinal}
          </Typography>
        ) : null}
        <NameLink
          to={experimentUrl(context.datasetId, first.experiment_id, {
            runs: experimentRunsOf(context.allRuns, first.experiment_id),
          })}
          title={first.experiment_name}
        >
          <Highlight text={first.experiment_name} q={context.q} />
        </NameLink>
        {isLatest && <Pill tone="primary">Latest</Pill>}
        <ExperimentTagChips experimentId={first.experiment_id} />
        {context.baselineRun &&
        runs.some((r) => r.run_id === context.baselineRun.run_id) ? (
          <CustomTooltip
            show
            size="small"
            arrow
            title={`${context.baselineRun.label} is in this experiment; it is pinned at the top so every delta reads against it.`}
          >
            <Typography
              component="span"
              typography="s3"
              color="text.secondary"
              noWrap
              data-testid="lb-group-baseline-marker"
              sx={{ flexShrink: 0 }}
            >
              {`${context.baselineRun.label} baseline ↑`}
            </Typography>
          </CustomTooltip>
        ) : null}
      </Stack>
      {/* EL#35: the full description on hover. IT-R2 G2 P1: an
          experiment of several kinds lists them as chips (`LangGraph ·
          OpenAI Agents SDK · CrewAI · …`), so `which experiment has CrewAI?`
          is answered on the group row; the description moves to the
          tooltip. */}
      <CustomTooltip
        show={!!first.experiment_description || showSources}
        title={
          showSources ? (
            // Every source with its mark, so the ones behind `+N` show too.
            <Stack gap={0.5} data-testid="lb-group-sources-tip">
              {first.experiment_description ? (
                <span>{first.experiment_description}</span>
              ) : null}
              {sources.map((x) => (
                <Stack
                  key={x.name}
                  direction="row"
                  alignItems="center"
                  gap={0.75}
                >
                  {x.entryId ? (
                    <SourceLogo entryId={x.entryId} size={16} />
                  ) : null}
                  <span>{x.name}</span>
                </Stack>
              ))}
            </Stack>
          ) : (
            first.experiment_description ?? ""
          )
        }
        size="small"
        placement="bottom-start"
        arrow
        enterDelay={400}
      >
        {showSources ? (
          <Stack
            ref={chipsRef}
            direction="row"
            alignItems="center"
            gap={0.5}
            data-testid="lb-group-sources"
            sx={{
              minWidth: 0,
              position: "relative",
              height: 18,
              overflow: "hidden",
              whiteSpace: "nowrap",
            }}
          >
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ flexShrink: 0, lineHeight: "18px" }}
            >
              {fmtCount(runs.length, "run")}
            </Typography>
            {shownSources.map((x) => (
              <Box
                key={x.name}
                component="span"
                data-source={x.name}
                sx={chipSx}
              >
                {x.entryId ? (
                  <SourceLogo entryId={x.entryId} size={16} />
                ) : null}
                {x.name}
              </Box>
            ))}
            {hiddenCount ? (
              <Box
                component="span"
                data-testid="lb-group-sources-more"
                aria-label={`Also: ${hiddenNames.join(", ")}`}
                sx={{ ...chipSx, fontWeight: 500 }}
              >
                {`+${hiddenCount}`}
              </Box>
            ) : null}
            {/* Every chip once, unseen: what the fitting count measures. */}
            <Stack
              ref={measureRef}
              direction="row"
              alignItems="center"
              aria-hidden
              sx={{
                position: "absolute",
                left: 0,
                top: 0,
                visibility: "hidden",
                pointerEvents: "none",
                whiteSpace: "nowrap",
              }}
            >
              <Typography
                typography="s3"
                sx={{ flexShrink: 0, lineHeight: "18px" }}
              >
                {fmtCount(runs.length, "run")}
              </Typography>
              {sources.map((x) => (
                <Box
                  key={x.name}
                  component="span"
                  data-measure-source={x.name}
                  sx={chipSx}
                >
                  {x.entryId ? (
                    <SourceLogo entryId={x.entryId} size={16} />
                  ) : null}
                  {x.name}
                </Box>
              ))}
            </Stack>
          </Stack>
        ) : (
          <Typography typography="s3" color="text.secondary" noWrap>
            {[fmtCount(runs.length, "run"), first.experiment_description]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        )}
      </CustomTooltip>
    </Stack>
  );
}

/**
 * IT-R2 G2 P1/P8: what ran in an experiment, once each, in run order:
 * `LangGraph`, `OpenAI Agents SDK`, `Claude Code`…; a voice agent run on
 * text reads `<platform> · Text only`, a spoken prompt `Prompt + speech`.
 */
export function experimentSources(runs) {
  const seen = new Map();
  (runs ?? []).forEach((r) => {
    const src = provenanceOf(r)?.source ?? {};
    let name = src.name ?? kindLabel(runKindOf(r));
    let special = false;
    if (r?.target_type === "tts") {
      name = "Prompt + speech";
      special = true;
    } else if (runKindOf(r) === "voice" && outputModalityOf(r) === "text") {
      name = `${name} · Text only`;
      special = true;
    }
    if (!name || seen.has(name)) return;
    seen.set(name, { name, entryId: src.entryId ?? null, special });
  });
  return [...seen.values()];
}

ExperimentGroupName.propTypes = {
  node: PropTypes.object.isRequired,
  context: PropTypes.object.isRequired,
};

/** Any other group-by: the group value and its run count. */
function PlainGroupName({ node }) {
  const runs = node.allLeafChildren ?? [];
  const label = runs[0]?.data?.__group_label ?? node.key;
  return (
    <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
      <Typography typography="s1" fontWeight="fontWeightMedium" noWrap>
        {label}
      </Typography>
      <Typography typography="s3" color="text.secondary" sx={{ flexShrink: 0 }}>
        {fmtCount(runs.length, "run")}
      </Typography>
    </Stack>
  );
}

PlainGroupName.propTypes = { node: PropTypes.object.isRequired };

/**
 * The pinned "Experiment" column (§3.7): run chip, what the run ran and the
 * Baseline / Latest pills. A run row links to the experiment page with
 * `run=`; a group row links to the experiment. Names are real links, so
 * Cmd/middle-click opens a new tab.
 */
const ExperimentNameCell = (params) => {
  const { node, data, context } = useCell(params);
  if (node.group) {
    return context.group === "experiment" ? (
      <ExperimentGroupName node={node} context={context} />
    ) : (
      <PlainGroupName node={node} />
    );
  }
  if (!data) return null;
  const pinnedRow = node.rowPinned === "top";
  const isBaseline = data.run_id === context.baselineId;
  const siblings = context.visibleByExperiment[data.experiment_id] ?? [];
  const underExperiment =
    context.group === "experiment" && !pinnedRow && siblings.length > 1;
  const isLatest =
    !underExperiment &&
    !pinnedRow &&
    data.experiment_id === context.latestExperimentId;
  const to = experimentUrl(context.datasetId, data.experiment_id, {
    runId: data.run_id,
  });
  // A run of an experiment listed under "Other experiments" says which
  // experiment it belongs to, as a link that opens it there.
  const otherExperiment = (
    <OtherExperimentRunLink
      experimentId={data.experiment_id}
      datasetId={context.datasetId}
      fallbackName={data.experiment_name}
    />
  );

  const nested =
    Boolean(context.iter?.enabled) && (data.__iterations || data.__depth === 1);
  const identity = runTitleOf(data);
  // R4 (lane Rb, D26): a run is named by its agent and version; the
  // experiment name stays on the group row (and in the tooltip).
  if (underExperiment) {
    const title = identity ?? distinctConfigLine(data, siblings);
    return (
      <Stack
        direction="row"
        alignItems="center"
        sx={{ minWidth: 0, height: "100%", py: nested ? 0 : 0.5 }}
      >
        <RunIdentity
          data={data}
          context={context}
          oneLine={nested}
          isBaseline={isBaseline}
          afterLabel={<RunsCountChip run={data} />}
          link={() => (
            <NameLink
              to={to}
              bold={Boolean(identity)}
              title={`${title} · ${runConfigLine(data)}`}
            >
              <Highlight text={title} q={context.q} />
            </NameLink>
          )}
          pills={otherExperiment}
        />
      </Stack>
    );
  }

  // D26: every run row leads with its agent and version (the experiment
  // name moves to the tooltip); runs without a version keep the name.
  const title = identity ?? data.experiment_name;
  const versionChip = Boolean(context.numbers?.chips?.[data.run_id]);
  return (
    <Stack
      direction="row"
      alignItems="center"
      sx={{ minWidth: 0, height: "100%", py: nested ? 0 : 0.5 }}
    >
      {pinnedRow && context.toggleBaselineTick ? (
        <Checkbox
          size="small"
          checked={Boolean(context.baselineTicked)}
          onClick={(e) => e.stopPropagation()}
          onChange={() => context.toggleBaselineTick()}
          inputProps={{
            "aria-label": `Select ${data.label} (baseline) to compare`,
          }}
          data-testid="baseline-checkbox"
          className="lb-baseline-tick"
          data-row-link
          sx={{ p: 0.5, ml: -0.5, mr: 0.25 }}
        />
      ) : null}
      <RunIdentity
        data={data}
        context={context}
        oneLine={nested}
        isBaseline={isBaseline && !pinnedRow}
        isPinned={isBaseline && context.baselineSource === "pinned"}
        afterLabel={<RunsCountChip run={data} />}
        link={() => (
          <NameLink
            to={to}
            title={`${data.experiment_name} · ${runConfigLine(data)}`}
          >
            <Highlight text={title} q={context.q} />
          </NameLink>
        )}
        pills={
          <>
            {isLatest && !versionChip && <Pill tone="primary">Latest</Pill>}
            <ExperimentTagChips experimentId={data.experiment_id} />
            {otherExperiment}
            {pinnedRow && (
              <Pill
                title={
                  context.baselineSource === "pinned"
                    ? "Pinned dataset baseline. Every delta on this page is measured against it."
                    : context.baselineSource === "url"
                      ? "Baseline run for this view. Every delta is measured against it."
                      : "Auto: the earliest completed run. Pin a run to fix the baseline."
                }
              >
                {context.baselineSource === "auto"
                  ? "Auto baseline"
                  : context.baselineSource === "pinned"
                    ? "Baseline · current"
                    : "Baseline"}
              </Pill>
            )}
          </>
        }
      />
    </Stack>
  );
};

export default ExperimentNameCell;
