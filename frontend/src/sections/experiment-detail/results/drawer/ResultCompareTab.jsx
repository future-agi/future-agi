import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Divider,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import ProductionRetestCard from "./ProductionRetestCard";
import BestOfCard from "src/sections/common/BestOf/BestOfCard";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import TextDiff, {
  TextDiffLegend,
} from "src/sections/common/ValueViewer/TextDiff";
import useValueViewMode from "src/sections/common/ValueViewer/useValueViewMode";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import AddToQueueDialog from "src/sections/annotations/queues/components/add-to-queue-dialog";
import { useRerunResultCells } from "src/api/develop/run-results";
import { RESULT_DRAWER_TAB_PROPTYPES } from "./ResultDrawerTabs.propTypes";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import CopyButton from "src/sections/common/ux/CopyButton";
import {
  expectedChecklist,
  expectedSentence,
  primaryPassMetric,
  trialOutcome,
} from "../resultsSimple";
import OutputCard from "./OutputCard";
import ModalityLens from "src/sections/common/Lenses/ModalityLens";
import { lensKindOf } from "src/sections/common/Lenses/lensKind";
import ScoreMatrix, { hasTrialGlyphs } from "./ScoreMatrix";
import ScoreCard from "./ScoreCard";
import AddResultsToDatasetDialog from "../AddResultsToDatasetDialog";
import { diffSource } from "../cells/OutputCell";
import { outputTextOrNull } from "../outputText";
// HO results R4: the reviewers' corrected answers on this row.
import CorrectedOutputsBlock from "src/sections/develop-detail/Review/CorrectedOutputsBlock";
// HO results R5: score the pair here (1 / 2 / 3), J / K move rows.
import PairVoteStrip from "src/sections/develop-detail/Review/PairVoteStrip";
import { usePairVote } from "./usePairVote";
// HO results R10 (W5 C-H6): Input leads with the grid's preview field.
import usePreviewField from "./usePreviewField";
import useDatasetColumnsLite from "src/sections/develop/useDatasetColumnsLite";
import { columnNamesById, displayInputs } from "../cells/displayInputs";

const ALL_RUNS_KEY = "fa.results.drawer.allRuns";

/**
 * iterate r3 G3 (P1): `Outputs · #7B and baseline #1A` while the drawer
 * shows the pair (names, never `2 of 5`); `Outputs` with every run.
 */
export function pairOutputsLabel({ shown, total, focused, baseline }) {
  if (!(total > shown)) return "Outputs";
  if (shown === 2 && focused && baseline)
    return `Outputs · ${focused} and baseline ${baseline}`;
  if (shown === 1 && (baseline || focused))
    return `Outputs · ${baseline ? `baseline ${baseline}` : focused}`;
  return `Outputs · ${shown} of ${total}`;
}

const SectionTitle = ({ children, right }) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={1}
    sx={{ mb: 0.75, minHeight: 24 }}
  >
    <Typography
      typography="s2"
      fontWeight="fontWeightSemiBold"
      color="text.secondary"
      sx={{ textTransform: "uppercase", letterSpacing: "0.5px" }}
    >
      {children}
    </Typography>
    <Box sx={{ flex: 1 }} />
    {right}
  </Stack>
);
SectionTitle.propTypes = { children: PropTypes.node, right: PropTypes.node };

const Field = ({ name, value, mode }) => (
  <Box sx={{ mb: 1 }}>
    <Typography typography="s2" color="text.secondary" sx={{ mb: 0.25 }}>
      {name}
    </Typography>
    <ValueViewer value={value} mode={mode} emptyLabel="empty" chatSwitch />
  </Box>
);
Field.propTypes = {
  name: PropTypes.string,
  value: PropTypes.any,
  mode: PropTypes.string,
};

const SIDE_BY_SIDE_MIN = 720;
const CARD_MIN = 340;

/**
 * Compare tab of the result drawer (PRODUCT-SPEC §3.11): Input, Expected,
 * one output card per run (side by side when there is room), the score
 * matrix with a ScoreCard, and row actions.
 */
const ResultCompareTab = ({
  datasetId,
  row,
  runs,
  baselineId,
  focusedRunId,
  trial,
  summary,
  colors,
  diff,
  onDiffChange,
  onTrialChange,
  onOpenTab,
  readOnly,
  rowIds,
}) => {
  const navigate = useNavigate();
  // A3-14: `Show eval definitions` always there, collapsed, remembered.
  const [drawerPrefs, patchDrawer] = useLayoutPrefs("resultDrawer");
  const showDefs = Boolean(drawerPrefs.extras?.evalDefs);
  const setShowDefs = (fn) =>
    patchDrawer({ extras: { evalDefs: fn(showDefs) ? true : undefined } });
  // iterate r3 G3 (P1): `Show all runs` is remembered for the session.
  const [allRunsPicked, setAllRunsPickedState] = useState(() => {
    try {
      return window.sessionStorage.getItem(ALL_RUNS_KEY) === "1";
    } catch {
      return false;
    }
  });
  const setAllRunsPicked = (fn) =>
    setAllRunsPickedState((v) => {
      const next = typeof fn === "function" ? fn(v) : fn;
      try {
        window.sessionStorage.setItem(ALL_RUNS_KEY, next ? "1" : "0");
      } catch {
        /* private mode: per drawer only */
      }
      return next;
    });
  const [mode, setMode] = useValueViewMode();
  const [width, setWidth] = useState(0);
  const [showAllInputs, setShowAllInputs] = useState(false);
  const [showExpectedDiff, setShowExpectedDiff] = useState(false);
  // P9: with the media lens shown, the output cards fold to one line each.
  const [cardsOpen, setCardsOpen] = useState(false);
  const [card, setCard] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [queueAnchor, setQueueAnchor] = useState(null);
  const [rerunAnchor, setRerunAnchor] = useState(null);
  const boxRef = useRef(null);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver((entries) =>
      setWidth(entries[0]?.contentRect?.width ?? 0),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // A different row closes the open ScoreCard.
  useEffect(() => setCard(null), [row?.example_id]);
  const pairVote = usePairVote({
    datasetId,
    exampleId: row?.example_id,
    runs,
    baselineId,
    focusedRunId,
  });

  const rerun = useRerunResultCells(datasetId, {
    onSuccess: () =>
      enqueueSnackbar("Re-running this row", { variant: "success" }),
  });

  const ordered = useMemo(() => {
    const base = runs.find((r) => r.run_id === baselineId);
    return base ? [base, ...runs.filter((r) => r.run_id !== baselineId)] : runs;
  }, [runs, baselineId]);
  // A3-13, width only: every run (≤ 3) side by side when each gets
  // CARD_MIN; else the baseline and the focused run, with `Show all runs`.
  const pair = ordered.filter(
    (r) => r.run_id === baselineId || r.run_id === focusedRunId,
  );
  const allFit =
    ordered.length <= 3 &&
    width >= Math.max(SIDE_BY_SIDE_MIN, ordered.length * CARD_MIN);
  const canShowAll = !allFit && ordered.length > pair.length;
  const showAll = allFit || (canShowAll && allRunsPicked);
  const shownRuns = showAll ? ordered : pair;
  const sideBySide = width >= shownRuns.length * CARD_MIN;

  // A native run's row stores its input as `{values: {<column id>: value}}`:
  // show each value under its dataset column name, as the grid's Input cell
  // does. The names come from the dataset columns read the grid already
  // made (same cache entry; read only for such a row, never refetched here).
  const nativeInputs = displayInputs(row.inputs) !== row.inputs;
  const { data: datasetColumns } = useDatasetColumnsLite(datasetId, {
    enabled: nativeInputs,
    refetchOnMount: false,
  });
  const inputNames = useMemo(
    () => columnNamesById(datasetColumns),
    [datasetColumns],
  );
  const shownInputs = useMemo(
    () => displayInputs(row.inputs, inputNames),
    [row.inputs, inputNames],
  );
  // The row as the output cards read it (prompt / agent prefill variables).
  const shownRow = useMemo(
    () => (shownInputs === row.inputs ? row : { ...row, inputs: shownInputs }),
    [row, shownInputs],
  );
  const inputs = Object.entries(shownInputs ?? {});
  const filledInputs = inputs.filter(
    ([, v]) => v != null && !(Array.isArray(v) && !v.length) && v !== "",
  );
  const preview = usePreviewField(datasetId, row, rowIds, inputNames);
  const visibleInputs = showAllInputs
    ? inputs
    : preview.field
      ? filledInputs.filter(([name]) => name === preview.field)
      : filledInputs.slice(0, 1);
  const expected = row.expected ?? {};
  const expectedName = expected.primary;
  const expectedValue = expectedName ? expected.value?.[expectedName] : null;
  const changedIn = expected.changed_in?.[0];
  const primary = primaryPassMetric(summary?.metrics);
  const checklist = expectedChecklist(expected, row.results?.[focusedRunId], {
    trial,
    metricKey: primary?.key,
  });
  const focusedLabel = runs.find((r) => r.run_id === focusedRunId)?.label;
  const metrics = (summary?.metrics ?? []).filter((m) =>
    ordered.some((r) => row.results?.[r.run_id]?.scores?.[m.key]),
  );
  const cardDef = card && metrics.find((m) => m.key === card.metric);
  const cardRun = card && runs.find((r) => r.run_id === card.run);

  const diffFor = (run) =>
    diff === "off"
      ? null
      : diff === "expected"
        ? diffSource(row, run.run_id, { diff, baselineId })
        : run.run_id === baselineId
          ? null
          : outputTextOrNull(
              row.results?.[baselineId]?.trials_detail?.[0]?.output ??
                row.results?.[baselineId]?.output,
            );

  // Two runs with media outputs → the modality lens.
  const lensValues =
    shownRuns.length === 2
      ? shownRuns.map((run) => {
          const r = row.results?.[run.run_id];
          const d = r?.trials_detail ?? [];
          const t =
            run.run_id === focusedRunId
              ? d[Math.min(trial, d.length) - 1] ?? d[0]
              : d[0];
          return outputTextOrNull(t?.output ?? r?.output);
        })
      : null;
  const lensKindRaw = lensValues ? lensKindOf(lensValues) : null;
  const lensKind = lensKindRaw && lensKindRaw !== "json" ? lensKindRaw : null;
  const foldCards = Boolean(lensKind) && !cardsOpen;

  return (
    <Stack spacing={2} ref={boxRef} sx={{ p: 2 }}>
      {/* iterate r2 G3 (P1): a re-test of a production failure leads. */}
      <ProductionRetestCard
        row={row}
        run={runs.find((r) => r.run_id === focusedRunId) ?? runs[0]}
        metrics={summary?.metrics}
      />
      {/* W5 B-H6: this row's Best of N verdict, order and scores. */}
      <BestOfCard
        datasetId={datasetId}
        exampleId={row.example_id}
        runs={runs}
      />
      <Box>
        <SectionTitle
          right={
            <Stack direction="row" alignItems="center" gap={1}>
              {diff !== "off" && <TextDiffLegend />}
              {/* IT-R5 G3 (P5): `Diff vs: Off · Baseline · Expected` on
                  one line (short labels; the long ones stay as names). */}
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ whiteSpace: "nowrap" }}
              >
                Diff vs:
              </Typography>
              <ToggleButtonGroup
                size="small"
                exclusive
                value={diff}
                onChange={(_, v) => v && onDiffChange(v)}
                aria-label="Diff vs"
                sx={{
                  flexShrink: 0,
                  "& .MuiToggleButton-root": {
                    py: 0.25,
                    px: 1,
                    typography: "s2",
                    textTransform: "none",
                    whiteSpace: "nowrap",
                  },
                }}
              >
                <ToggleButton value="off">Off</ToggleButton>
                <ToggleButton
                  value="baseline"
                  disabled={runs.length < 2}
                  aria-label="vs baseline run"
                >
                  Baseline
                </ToggleButton>
                <ToggleButton
                  value="expected"
                  disabled={!expectedName}
                  aria-label="vs expected"
                >
                  Expected
                </ToggleButton>
              </ToggleButtonGroup>
            </Stack>
          }
        >
          {(() => {
            const [head, ...rest] = pairOutputsLabel({
              shown: shownRuns.length,
              total: ordered.length,
              focused: runs.find((r) => r.run_id === focusedRunId)?.label,
              baseline: runs.find((r) => r.run_id === baselineId)?.label,
            }).split(" · ");
            return (
              <>
                {head}
                {rest.length ? (
                  <Box
                    component="span"
                    data-testid="drawer-outputs-pair"
                    sx={{ textTransform: "none", letterSpacing: 0 }}
                  >
                    {` · ${rest.join(" · ")}`}
                  </Box>
                ) : null}
              </>
            );
          })()}
          {canShowAll && (
            <Button
              size="small"
              onClick={() => setAllRunsPicked((v) => !v)}
              aria-pressed={showAll}
              data-testid="drawer-show-all-runs"
              sx={{
                typography: "s2",
                textTransform: "none",
                letterSpacing: 0,
                ml: 1,
                px: 0.75,
                minWidth: 0,
                height: 22,
              }}
            >
              {showAll
                ? `Show ${pair.length === 2 ? `${pair.map((r) => r.label).join(" and ")} only` : `${pair.length} runs`}`
                : "Show all runs"}
            </Button>
          )}
        </SectionTitle>
        {foldCards ? (
          <Stack gap={0.5} data-testid="drawer-output-summary">
            {shownRuns.map((run) => {
              const o = trialOutcome(row.results?.[run.run_id], primary?.key);
              const c = colors?.[run.run_id];
              return (
                <Stack
                  key={run.run_id}
                  direction="row"
                  alignItems="center"
                  gap={0.75}
                  sx={{ minWidth: 0 }}
                >
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      flexShrink: 0,
                      bgcolor: c?.solid ?? c ?? "text.disabled",
                    }}
                  />
                  <Typography typography="s2" fontWeight={600} noWrap>
                    {run.label}
                  </Typography>
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    noWrap
                    sx={{ minWidth: 0 }}
                  >
                    {[run.name, run.run_id === baselineId ? "baseline" : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </Typography>
                  <Typography
                    typography="s2"
                    sx={{
                      ml: "auto",
                      flexShrink: 0,
                      color:
                        o.tone === "pass"
                          ? "green.600"
                          : o.tone === "fail"
                            ? "red.600"
                            : "text.secondary",
                    }}
                  >
                    {o.label}
                  </Typography>
                </Stack>
              );
            })}
            <Button
              size="small"
              onClick={() => setCardsOpen(true)}
              sx={{ alignSelf: "flex-start", typography: "s2", px: 0.5 }}
              data-testid="drawer-show-output-cards"
            >
              Show the output cards
            </Button>
          </Stack>
        ) : (
          <Stack
            direction={sideBySide ? "row" : "column"}
            gap={1.25}
            alignItems="stretch"
          >
            {shownRuns.map((run) => (
              <OutputCard
                key={run.run_id}
                run={run}
                color={colors?.[run.run_id]}
                isBaseline={run.run_id === baselineId && runs.length > 1}
                result={row.results?.[run.run_id]}
                baseResult={row.results?.[baselineId]}
                trial={run.run_id === focusedRunId ? trial : 1}
                onTrialChange={(t) =>
                  run.run_id === focusedRunId && onTrialChange(t)
                }
                diffFrom={diffFor(run)}
                mode={mode}
                row={shownRow}
                dots={
                  trialOutcome(row.results?.[run.run_id], primary?.key).dots
                }
                focused={run.run_id === focusedRunId && runs.length > 1}
                onViewTrace={() =>
                  onOpenTab("trace", {
                    run: run.run_id,
                    trial:
                      run.run_id === focusedRunId && trial > 1 ? trial : null,
                  })
                }
              />
            ))}
          </Stack>
        )}
        <Box sx={{ mt: 1.25, "&:empty": { display: "none" } }}>
          <PairVoteStrip vote={pairVote} />
        </Box>
        <Box sx={{ mt: 1.25, "&:empty": { display: "none" } }}>
          <CorrectedOutputsBlock
            row={row}
            runs={shownRuns}
            colors={colors}
            mode={mode}
          />
        </Box>
        {lensKind ? (
          <Box sx={{ mt: 1.25 }} data-testid="drawer-modality-lens">
            <ModalityLens
              kind={lensKind}
              rowId={row.example_id}
              lanes={shownRuns.map((run, i) => ({
                runId: run.run_id,
                label: run.label ?? run.run_id,
                color: colors?.[run.run_id]?.solid ?? colors?.[run.run_id],
                isBaseline: run.run_id === baselineId,
                value: lensValues[i],
              }))}
              // The one-line summaries above give the viewer the height.
              compact={!foldCards}
            />
          </Box>
        ) : null}
      </Box>

      <Box>
        <SectionTitle
          right={
            <Button
              size="small"
              endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
              onClick={() =>
                navigate(
                  `/dashboard/develop/${datasetId}?tab=data&row=${row.example_id}&edit=expected`,
                )
              }
              sx={{ typography: "s2", color: "text.secondary" }}
            >
              Edit in dataset
            </Button>
          }
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            component="span"
          >
            <span>Expected</span>
            {expected.version && <VersionNumber number={expected.version} />}
            {expectedSentence(expected) && (
              <CopyButton
                value={expectedSentence(expected, { prefix: true })}
                tooltip="Copy expected"
              />
            )}
          </Stack>
        </SectionTitle>
        {changedIn && (
          <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 0.75 }}>
            <Chip
              size="small"
              label={`Updated in v${changedIn}`}
              sx={{
                height: 22,
                borderRadius: 0.5,
                typography: "s3",
                bgcolor: (t) => `${t.palette.warning.main}1F`,
                color: "warning.dark",
              }}
            />
            <Button
              size="small"
              onClick={() => setShowExpectedDiff((v) => !v)}
              sx={{ typography: "s2", px: 0.5 }}
            >
              {showExpectedDiff ? "Hide diff" : "Show diff"}
            </Button>
          </Stack>
        )}
        {checklist.length > 0 ? (
          <Stack spacing={0.5} data-testid="expected-checklist">
            {checklist.map((c) => (
              <Stack
                key={c.key}
                direction="row"
                alignItems="flex-start"
                gap={0.75}
                aria-label={`${c.passed === true ? "Passed" : c.passed === false ? "Failed" : "Not reached"}: ${c.text}`}
              >
                <Iconify
                  icon={
                    c.passed === true
                      ? "mdi:check"
                      : c.passed === false
                        ? "mdi:close"
                        : "mdi:minus"
                  }
                  width={16}
                  sx={{
                    mt: "2px",
                    flexShrink: 0,
                    color:
                      c.passed === true
                        ? "green.600"
                        : c.passed === false
                          ? "red.600"
                          : "text.disabled",
                  }}
                />
                <Typography
                  typography="s1"
                  color={c.passed == null ? "text.secondary" : "text.primary"}
                >
                  {c.text}
                  {c.passed == null && (
                    <Typography
                      component="span"
                      typography="s2"
                      color="text.disabled"
                    >
                      {" · not reached"}
                    </Typography>
                  )}
                </Typography>
              </Stack>
            ))}
            {focusedLabel && (
              <Typography typography="s3" color="text.secondary">
                {`Checked against ${focusedLabel}${trial > 1 ? `, trial ${trial}` : ""}.`}
              </Typography>
            )}
            <Box>
              <Button
                size="small"
                onClick={() => setShowDefs((v) => !v)}
                startIcon={
                  <Iconify
                    icon={showDefs ? "mdi:chevron-down" : "mdi:chevron-right"}
                    width={16}
                  />
                }
                sx={{ typography: "s2", px: 0.5 }}
                aria-expanded={showDefs}
              >
                {showDefs ? "Hide eval definitions" : "Show eval definitions"}
              </Button>
              {showDefs && (
                <ValueViewer
                  value={expectedName ? expectedValue : expected.value}
                  mode={mode}
                  emptyLabel="No expected value"
                />
              )}
            </Box>
          </Stack>
        ) : showExpectedDiff && changedIn ? (
          <TextDiff from={expectedValue} to={expected.latest?.[expectedName]} />
        ) : expectedName ? (
          <ValueViewer
            value={expectedValue}
            mode={mode}
            emptyLabel="No expected value"
          />
        ) : (
          <Typography typography="s2" color="text.disabled">
            This dataset has no reference column
          </Typography>
        )}
      </Box>

      <Box>
        <SectionTitle
          right={
            <ToggleButtonGroup
              size="small"
              exclusive
              value={mode}
              onChange={(_, v) => v && setMode(v)}
              sx={{
                "& .MuiToggleButton-root": {
                  py: 0.25,
                  px: 1,
                  typography: "s2",
                  textTransform: "none",
                },
              }}
            >
              <ToggleButton value="formatted">Formatted</ToggleButton>
              <ToggleButton value="raw">Raw</ToggleButton>
            </ToggleButtonGroup>
          }
        >
          <Stack direction="row" alignItems="center" gap={0.5} component="span">
            <span>Input</span>
            <CopyButton
              value={() =>
                filledInputs
                  .map(
                    ([n, v]) =>
                      `${n}: ${typeof v === "string" ? v : JSON.stringify(v)}`,
                  )
                  .join("\n")
              }
              tooltip="Copy input"
            />
          </Stack>
        </SectionTitle>
        {visibleInputs.map(([name, value]) => (
          <Field key={name} name={name} value={value} mode={mode} />
        ))}
        {inputs.length > visibleInputs.length && (
          <Button
            size="small"
            onClick={() => setShowAllInputs(true)}
            sx={{ typography: "s2", px: 0 }}
          >
            {`Show ${inputs.length - visibleInputs.length} more ${inputs.length - visibleInputs.length === 1 ? "field" : "fields"}`}
          </Button>
        )}
      </Box>

      {(metrics.length > 0 || hasTrialGlyphs(row, ordered)) && (
        <Box>
          <SectionTitle>Scores</SectionTitle>
          <ScoreMatrix
            metrics={metrics}
            runs={ordered}
            colors={colors ?? {}}
            baselineId={baselineId}
            row={row}
            active={card}
            onPick={(c) =>
              setCard((prev) =>
                prev?.metric === c.metric && prev?.run === c.run ? null : c,
              )
            }
          />
          {cardDef && cardRun && (
            <Box sx={{ mt: 1.25 }}>
              <ScoreCard
                def={cardDef}
                run={cardRun}
                color={colors?.[cardRun.run_id]}
                result={row.results?.[cardRun.run_id]}
                trial={cardRun.run_id === focusedRunId ? trial : 1}
                onTrialChange={(t) => {
                  if (cardRun.run_id !== focusedRunId)
                    onOpenTab("compare", { run: cardRun.run_id });
                  onTrialChange(t);
                }}
                uemId={cardDef.uem_ids?.[cardRun.run_id]}
                readOnly={readOnly}
                onClose={() => setCard(null)}
              />
            </Box>
          )}
        </Box>
      )}

      <Divider />
      <Stack direction="row" gap={1} flexWrap="wrap">
        <Button
          size="small"
          variant="outlined"
          disabled={readOnly}
          startIcon={<Iconify icon="mdi:plus" width={16} />}
          onClick={() => setAddOpen(true)}
        >
          Add to dataset
        </Button>
        <Button
          size="small"
          variant="outlined"
          disabled={readOnly}
          startIcon={<Iconify icon="jam:write" width={14} />}
          onClick={(e) => setQueueAnchor(e.currentTarget)}
        >
          Add to annotation queue
        </Button>
        <Button
          size="small"
          variant="outlined"
          disabled={readOnly}
          startIcon={<Iconify icon="mdi:refresh" width={16} />}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          onClick={(e) => setRerunAnchor(e.currentTarget)}
        >
          Re-run
        </Button>
        <Menu
          anchorEl={rerunAnchor}
          open={!!rerunAnchor}
          onClose={() => setRerunAnchor(null)}
        >
          <MenuItem
            onClick={() => {
              setRerunAnchor(null);
              rerun.mutate(
                runs.map((run) => ({ run, exampleId: row.example_id })),
              );
            }}
          >
            <ListItemText
              primary="This row, all runs"
              primaryTypographyProps={{ typography: "s1" }}
            />
          </MenuItem>
          {runs.map((run) => (
            <MenuItem
              key={run.run_id}
              onClick={() => {
                setRerunAnchor(null);
                rerun.mutate([{ run, exampleId: row.example_id }]);
              }}
            >
              <ListItemText
                primary={`This row, ${run.label} only`}
                primaryTypographyProps={{ typography: "s1" }}
              />
            </MenuItem>
          ))}
        </Menu>
      </Stack>

      <AddResultsToDatasetDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        datasetId={datasetId}
        datasetName={summary?.dataset?.name}
        exampleIds={[row.example_id]}
        rows={[row]}
        runs={runs}
        baselineId={baselineId}
        pills={[]}
        focusedRunId={focusedRunId}
        metrics={summary?.metrics ?? []}
      />
      {queueAnchor && (
        <AddToQueueDialog
          anchorEl={queueAnchor}
          onClose={() => setQueueAnchor(null)}
          sourceType="dataset_row"
          sourceIds={[row.example_id]}
          itemName="row"
          onSuccess={() => setQueueAnchor(null)}
        />
      )}
    </Stack>
  );
};

ResultCompareTab.propTypes = {
  ...RESULT_DRAWER_TAB_PROPTYPES,
  summary: PropTypes.object,
  colors: PropTypes.object,
  diff: PropTypes.string,
  onDiffChange: PropTypes.func,
  onTrialChange: PropTypes.func,
  onOpenTab: PropTypes.func,
  readOnly: PropTypes.bool,
  rowIds: PropTypes.array,
};

export default ResultCompareTab;
