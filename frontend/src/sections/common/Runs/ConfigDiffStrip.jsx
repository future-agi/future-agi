import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Chip,
  IconButton,
  Menu,
  MenuItem,
  Popover,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { runLabel, runShortName, runSpokenName } from "./runLabel";
import { configDiff, configDiffSentence } from "./configDiff";
import ConfigDiffDetail from "./ConfigDiffDetail";
import IsolateMenu from "./IsolateMenu";
import HoverCard from "src/sections/common/ux/oneview/HoverCard";

const Dot = () => (
  <Typography
    component="span"
    typography="s2"
    color="text.disabled"
    aria-hidden
    sx={{ flexShrink: 0 }}
  >
    ·
  </Typography>
);

const MAX_CHIPS = 4;
/** R4: the short chip copy (`gpt-4o-mini → gpt-4.1-mini`); R2 chips fall back to `label`. */
const chipText = (c) => c?.text ?? c?.label ?? "";

const KIND_WORD = {
  prompt: "prompt",
  agent: "agent",
  code: "code",
  harness: "harness",
  traces: "traces",
  http: "HTTP",
};
const kindWord = (run) =>
  KIND_WORD[run?.config?.kind ?? run?.kind ?? "prompt"] ??
  String(run?.config?.kind ?? run?.kind ?? "prompt");

/**
 * R3 (SVP-4.8): the soft sentence for runs of different kinds:
 * `Comparing different setups (agent vs prompt). Differences can't be
 * pinned on one change.`; other strips keep configDiffSentence.
 */
export function kindSentence(a, b) {
  return `Comparing different setups (${kindWord(a)} vs ${kindWord(b)}). Differences can't be pinned on one change.`;
}

const linkButtonSx = {
  height: 22,
  px: 0.75,
  minWidth: 0,
  typography: "s2",
  flexShrink: 0,
};

const runOf = (ref, runs) => {
  if (!ref) return null;
  if (typeof ref === "object") return ref;
  return (runs ?? []).find((r) => r.run_id === ref) ?? null;
};

const chipSx = {
  height: 22,
  borderRadius: 0.5,
  typography: "s2",
  "& .MuiChip-label": { px: 0.75 },
};

/**
 * One 24 px line (one view, A3-01): `What changed #2B → #2A · kind Agent
 * graph → Prompt + tools · +3 · What differs ›` · `Isolate ▾`. The first
 * chip shows inline (two at ≥ 1600 px); `+3` is a HoverCard listing every
 * differing setting and the "cause is not isolated" sentence; a chip opens
 * its diff (560 px popover); the run names pick the pair (`#2A` → another
 * run, `#2B` → another baseline). `compact` (tooltips, banners) keeps the
 * short chip row. Renders nothing when either run has no `config` or
 * nothing differs.
 *
 * Props (frozen, R2 manifest row 40; later keys additive):
 * @param {string|object} props.baseline  run id (looked up in `runs`) or run
 * @param {string|object} props.candidate  run id or run
 * @param {object[]} [props.runs]  R1 runs with `config` (useRunConfigs)
 * @param {(chip: object) => void} [props.onFocus]  chip click
 * @param {boolean} [props.compact]  no sentence, fewer chips
 * @param {string[]} [props.candidates]  runs in view the strip can focus
 * @param {(runId: string) => void} [props.onCandidate]  pick the pair's run (`cand=`)
 * @param {(runId: string) => void} [props.onBaseline]  pick the baseline
 * @param {boolean} [props.readOnly]  viewers cannot start an isolating run
 * @param {boolean} [props.runsLine]  IT-R5 G6 (additive): show the
 *   `3 runs: …` line for 3+ runs of different kinds (default: folded into
 *   the tab row's runs chip)
 */
const ConfigDiffStrip = ({
  baseline,
  candidate,
  runs,
  onFocus,
  compact,
  candidates,
  onCandidate,
  onBaseline,
  readOnly,
  runsLine = false,
}) => {
  const a = runOf(baseline, runs);
  const b = runOf(candidate, runs);
  const [pop, setPop] = useState(null); // {anchor, index}
  const [runsAnchor, setRunsAnchor] = useState(null);
  const [baseAnchor, setBaseAnchor] = useState(null);
  const [pairOpen, setPairOpen] = useState(false);
  const moreRef = useRef(null);
  const wide = useMediaQuery("(min-width:1600px)");
  const chips = useMemo(
    () => (a?.config && b?.config ? configDiff(a, b) : []),
    [a, b],
  );
  if (!chips.length) return null;
  const max = compact ? 2 : MAX_CHIPS;
  const shown = chips.slice(0, max);
  const rest = chips.slice(max);
  const isKind = chips.some((c) => c.axis === "kind");
  // R4: `Changed: framework (Agent graph → LangGraph), model (…), tools (…).`
  // Across kinds, the R3 caution follows it.
  const sentence = compact
    ? null
    : [configDiffSentence(chips), isKind ? kindSentence(a, b) : null]
        .filter(Boolean)
        .join(" ");
  const labelA = runLabel(a);
  const labelB = runLabel(b);
  // IT-R2 G2 P9: an experiment of 3+ runs of different kinds is not "one
  // change from #4A to #4C": the line names what ran (`3 runs: the coding harness Code
  // · the coding harness CLI · TS agent ▸`); ▸ opens the pair's What changed.
  const inView = (candidates ?? [])
    .map((id) => (runs ?? []).find((r) => r.run_id === id))
    .filter(Boolean);
  // IT-R5 G6 P1: on the experiment page the names fold into the hover card
  // of the `5 runs` chip in the tab row (one header row less); the line
  // stays where the strip is asked for it (`runsLine`).
  if (!compact && !pairOpen && isKind && inView.length >= 3 && !runsLine)
    return null;
  if (!compact && !pairOpen && isKind && inView.length >= 3) {
    const names = [
      ...new Set(inView.map((r) => runSpokenName(r)).filter(Boolean)),
    ];
    const text = `${inView.length} runs: ${names.join(" · ")}`;
    return (
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{ px: 1, minWidth: 0, height: 24, flexWrap: "nowrap" }}
        data-testid="config-diff-strip"
        data-mode="runs"
      >
        <CustomTooltip
          show
          size="small"
          placement="bottom"
          title={`${text}. Different kinds of run, so there is no single change to read: open What changed for ${labelA} → ${labelB}, or pick a pair in Compare with….`}
        >
          <Typography typography="s2" color="text.secondary" noWrap>
            {text}
          </Typography>
        </CustomTooltip>
        <IconButton
          size="small"
          aria-label={`What changed ${labelA} → ${labelB}`}
          onClick={() => setPairOpen(true)}
          sx={{ p: 0.25 }}
        >
          <Iconify icon="mdi:chevron-right" width={16} />
        </IconButton>
      </Stack>
    );
  }
  const others = (candidates ?? []).filter((id) => id !== a?.run_id);
  const openChipAt = (anchor, index) => {
    if (!anchor) return;
    setPop({ anchor, index });
    onFocus?.(chips[index]);
  };
  const openChip = (e, index) => openChipAt(e.currentTarget, index);
  const active = pop ? chips[pop.index] : null;
  const pairText = `${labelA} → ${labelB}`;
  // R4 one view: up to 3 chips on the line (2 below 1600 px), then `+n`.
  const inline = chips.slice(0, wide ? 3 : 2);
  const more = chips.slice(inline.length);
  const canPick = others.length > 1 && Boolean(onCandidate);
  const baseChoices = (candidates ?? []).filter((id) => id !== a?.run_id);
  const canPickBase = Boolean(onBaseline) && baseChoices.length > 0;
  const runName = (label, { pick, tooltip, onOpen, testId }) =>
    pick ? (
      <CustomTooltip show size="small" placement="bottom" arrow title={tooltip}>
        <ButtonBase
          aria-haspopup="menu"
          onClick={onOpen}
          data-testid={testId}
          sx={{
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
            color: "text.primary",
            px: 0.5,
            height: 22,
            borderRadius: 0.5,
            flexShrink: 0,
            textDecoration: "underline dotted",
            textDecorationColor: (t) => t.palette.text.disabled,
            textUnderlineOffset: "3px",
            "&:hover": { bgcolor: "action.hover" },
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
            },
          }}
        >
          {label}
        </ButtonBase>
      </CustomTooltip>
    ) : (
      <Typography
        component="span"
        typography="s2"
        fontWeight="fontWeightSemiBold"
        noWrap
        sx={{ flexShrink: 0, px: 0.25 }}
      >
        {label}
      </Typography>
    );
  const runMenu = (anchor, onClose, ids, selectedId, onPick, text) => (
    <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={onClose}>
      {ids.map((id) => {
        const r = runOf(id, runs);
        return (
          <MenuItem
            key={id}
            selected={id === selectedId}
            onClick={() => {
              onClose();
              onPick(id);
            }}
            sx={{ typography: "s2" }}
          >
            {text(r, id)}
          </MenuItem>
        );
      })}
    </Menu>
  );
  const basePicker = (
    <>
      {runName(labelA, {
        pick: canPickBase,
        tooltip: `Baseline ${labelA}. Click to compare against another run.`,
        onOpen: (e) => setBaseAnchor(e.currentTarget),
        testId: "config-diff-base",
      })}
      {canPickBase &&
        runMenu(
          baseAnchor,
          () => setBaseAnchor(null),
          baseChoices,
          a?.run_id,
          onBaseline,
          (r, id) =>
            `Make ${runLabel(r) || id} the baseline ${runShortName(r)}`,
        )}
    </>
  );
  const candPicker = (
    <>
      {runName(labelB, {
        pick: canPick,
        tooltip: `One pair at a time: baseline ${labelA} vs ${labelB}. Click to pick another of the ${others.length} runs.`,
        onOpen: (e) => setRunsAnchor(e.currentTarget),
        testId: "config-diff-pair",
      })}
      {canPick &&
        runMenu(
          runsAnchor,
          () => setRunsAnchor(null),
          others,
          b?.run_id,
          onCandidate,
          (r, id) => `${labelA} → ${runLabel(r) || id} ${runShortName(r)}`,
        )}
    </>
  );
  const popover = (
    <Popover
      open={Boolean(pop)}
      anchorEl={pop?.anchor}
      onClose={() => setPop(null)}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{
        paper: {
          sx: {
            width: 560,
            maxHeight: 480,
            display: "flex",
            flexDirection: "column",
            mt: 0.5,
          },
        },
      }}
    >
      {active && (
        <>
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{
              px: 1.5,
              pt: 1.25,
              pb: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexWrap: "wrap",
              rowGap: 0.5,
            }}
          >
            <Typography
              typography="s2"
              fontWeight="fontWeightSemiBold"
              sx={{ mr: 0.5 }}
            >
              {`${labelA} → ${labelB}`}
            </Typography>
            {chips.map((c, i) => (
              <Chip
                key={`${c.axis}:${c.label}`}
                label={chipText(c)}
                size="small"
                variant={i === pop.index ? "filled" : "outlined"}
                color={i === pop.index ? "primary" : "default"}
                onClick={() => setPop((p) => ({ ...p, index: i }))}
                sx={{ ...chipSx, maxWidth: 240 }}
              />
            ))}
          </Stack>
          <Box sx={{ p: 1.5, overflow: "auto" }}>
            <ConfigDiffDetail
              chip={active}
              a={a}
              b={b}
              labelA={labelA}
              labelB={labelB}
            />
          </Box>
        </>
      )}
    </Popover>
  );

  if (compact)
    return (
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0, flexWrap: "nowrap" }}
        data-testid="config-diff-strip"
      >
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ flexShrink: 0 }}
        >
          {`What changed ${pairText}`}
        </Typography>
        <Stack
          direction="row"
          gap={0.5}
          alignItems="center"
          sx={{ minWidth: 0, overflow: "hidden" }}
        >
          {shown.map((c, i) => (
            <Chip
              key={`${c.axis}:${c.label}`}
              label={chipText(c)}
              variant="outlined"
              size="small"
              clickable
              onClick={(e) => openChip(e, i)}
              sx={{ ...chipSx, minWidth: 0, maxWidth: 360 }}
            />
          ))}
          {rest.length > 0 && (
            <Chip
              label={`+${rest.length} more`}
              variant="outlined"
              size="small"
              clickable
              onClick={(e) => openChip(e, max)}
              sx={{ ...chipSx, flexShrink: 0 }}
            />
          )}
        </Stack>
        {popover}
      </Stack>
    );

  // One view (A3-01): one 24 px line, chips in place.
  // `What changed #2B → #2A · kind Agent graph → Prompt + tools · +3 ·
  // What differs ›`. The run names pick the pair; `+3` is a HoverCard with
  // every differing setting and the "cause is not isolated" sentence.
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{ px: 1, minWidth: 0, height: 24, flexWrap: "nowrap" }}
      data-testid="config-diff-strip"
    >
      <Typography
        typography="s2"
        color="text.secondary"
        noWrap
        sx={{ flexShrink: 0, mr: 0.25 }}
      >
        What changed
      </Typography>
      {basePicker}
      <Typography
        component="span"
        typography="s2"
        color="text.disabled"
        aria-hidden
        sx={{ flexShrink: 0 }}
      >
        →
      </Typography>
      {candPicker}
      <Dot />
      <Stack
        direction="row"
        gap={0.5}
        alignItems="center"
        sx={{ minWidth: 0, overflow: "hidden", flexShrink: 1 }}
      >
        {inline.map((c, i) => (
          <CustomTooltip
            key={`${c.axis}:${c.label}`}
            show
            size="small"
            placement="bottom"
            arrow
            title={
              more.length === 0 && sentence
                ? `${chipText(c)} · click for the diff. ${sentence}`
                : `${chipText(c)} · click for the diff`
            }
          >
            <Chip
              label={chipText(c)}
              variant="outlined"
              size="small"
              clickable
              onClick={(e) => openChip(e, i)}
              sx={{ ...chipSx, minWidth: 0, maxWidth: 280 }}
            />
          </CustomTooltip>
        ))}
      </Stack>
      {more.length > 0 && (
        <>
          <Dot />
          <HoverCard
            trigger={
              <Box
                component="span"
                data-testid="config-diff-more"
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  px: 0.5,
                  borderRadius: 0.5,
                  border: "1px solid",
                  borderColor: "divider",
                  lineHeight: "20px",
                  display: "inline-block",
                  whiteSpace: "nowrap",
                  fontVariantNumeric: "tabular-nums",
                  "&:hover": { color: "text.primary" },
                }}
              >
                {`+${more.length}`}
              </Box>
            }
            triggerProps={{
              "aria-label": `${more.length} more ${more.length === 1 ? "setting differs" : "settings differ"}`,
            }}
            title={`What changed ${pairText}`}
            maxWidth={380}
          >
            <Stack gap={0.75}>
              <Stack direction="row" gap={0.5} sx={{ flexWrap: "wrap" }}>
                {chips.map((c, i) => (
                  <Chip
                    key={`${c.axis}:${c.label}`}
                    label={chipText(c)}
                    variant="outlined"
                    size="small"
                    clickable
                    onClick={() => openChipAt(moreRef.current, i)}
                    sx={{ ...chipSx, maxWidth: 340 }}
                  />
                ))}
              </Stack>
              {sentence && (
                <Typography typography="s2" color="text.secondary">
                  {sentence}
                </Typography>
              )}
              <Typography typography="s3" color="text.disabled">
                Click a setting for its diff
              </Typography>
            </Stack>
          </HoverCard>
          <Box ref={moreRef} component="span" sx={{ width: 0, height: 20 }} />
        </>
      )}
      <Dot />
      <CustomTooltip
        show
        size="small"
        placement="bottom"
        arrow
        title={`Every setting that differs between ${labelA} and ${labelB}`}
      >
        <Button
          size="small"
          onClick={(e) => openChip(e, 0)}
          endIcon={<Iconify icon="mdi:chevron-right" width={14} />}
          sx={{ ...linkButtonSx, "& .MuiButton-endIcon": { ml: 0.25 } }}
          data-testid="what-differs"
        >
          What differs
        </Button>
      </CustomTooltip>
      <IsolateMenu baseline={a} candidate={b} runs={runs} readOnly={readOnly} />
      {popover}
    </Stack>
  );
};

ConfigDiffStrip.propTypes = {
  runsLine: PropTypes.bool,
  baseline: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  candidate: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  runs: PropTypes.arrayOf(PropTypes.object),
  onFocus: PropTypes.func,
  compact: PropTypes.bool,
  candidates: PropTypes.arrayOf(PropTypes.string),
  onCandidate: PropTypes.func,
  onBaseline: PropTypes.func,
  readOnly: PropTypes.bool,
};

export default ConfigDiffStrip;
