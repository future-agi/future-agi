// MUST 6: `Speed and correctness` on voice datasets, above the
// R3 eval table. The answer first (the verdict lines), then one table per
// run on the one-view column shaping (default: Task completion, Latency p50,
// Latency p90, Kept talking, Cost per call, source; `Columns` adds p99, WER,
// talk ratio, duration p50; remembered; `Reset to default`), the text-only
// run in its own group, `Where the time goes` (stage bars, one summary line
// when folded), `By persona`, and the latency sources. Every number comes
// from voiceSummaryModel over the calls (§C10).
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useVoiceSummary } from "src/api/develop/r4";
import { useRunColors } from "src/sections/common/Runs/runColors";
import VerdictLine from "src/sections/common/ux/VerdictLine";
import Term from "src/sections/common/ux/Term";
import Section from "src/sections/common/ux/oneview/Section";
import useColumnLayout from "src/sections/common/ux/oneview/useColumnLayout";
import ColumnsMenu from "src/sections/common/ux/oneview/ColumnsMenu";
import {
  EMPTY,
  NUM_SX,
  fmtCost,
  fmtCount,
  fmtMs,
  fmtPct,
  fmtPts,
} from "src/sections/common/format/fmt";
import { VOCAB } from "src/sections/common/vocab";
import { compareRuns } from "src/sections/common/Runs/runNumbers";
import StageSplitBar from "./StageSplitBar";
import {
  costSplitText,
  fmtLatency,
  isTextOnly,
  personaGroups,
  sourceChipOf,
  sourcesText,
  stageLine,
  voiceMatrix,
  voiceRunStats,
  voiceRuns,
  voiceVerdictList,
} from "./voiceSummaryModel";

// isTextOnly is re-exported for callers that group runs the same way.
export { isTextOnly };

const SURFACE = "summary";

/** Table columns (one-view catalog; ids = the cells below). */
export const VOICE_COLUMNS = Object.freeze([
  { id: "run", label: "Run", locked: true },
  { id: "completion", label: VOCAB.TASK_COMPLETION },
  { id: "p50", label: VOCAB.LATENCY_P50 },
  { id: "p90", label: VOCAB.LATENCY_P90 },
  { id: "p99", label: VOCAB.LATENCY_P99, defaultVisible: false },
  { id: "kept", label: VOCAB.KEPT_TALKING },
  { id: "wer", label: "WER", defaultVisible: false },
  { id: "talk", label: "Talk ratio", defaultVisible: false },
  { id: "cost", label: "Cost per call" },
  { id: "duration", label: "Duration p50", defaultVisible: false },
  { id: "source", label: "Latency source" },
]);

const HEAD = {
  completion: <Term k="successRate">{VOCAB.TASK_COMPLETION}</Term>,
  p50: VOCAB.LATENCY_P50,
  p90: <Term k="latencyP90">{VOCAB.LATENCY_P90}</Term>,
  p99: VOCAB.LATENCY_P99,
  kept: <Term k="bargeIn">Kept talking</Term>,
  wer: <Term k="wer">WER</Term>,
  talk: <Term k="talkRatio">Talk ratio</Term>,
  cost: "Cost per call",
  duration: "Duration p50",
  source: "Source",
};

/** CSV of every column (hidden ones included). */
export function voiceCsv(rows) {
  const head = [
    "Run",
    "Name",
    "Task completion",
    "Calls completed",
    "Calls",
    "Latency p50 (ms)",
    "Latency p90 (ms)",
    "Latency p99 (ms)",
    "Kept talking",
    "Barge-ins",
    "Avg stop (ms)",
    "WER",
    "Talk ratio",
    "Cost per call (USD)",
    "Duration p50 (ms)",
    "Latency source",
  ];
  const esc = (v) =>
    v == null
      ? ""
      : /[",\n]/.test(String(v))
        ? `"${String(v).replace(/"/g, '""')}"`
        : String(v);
  const lines = rows.map((s) =>
    [
      s.label,
      s.fullName,
      s.completion.rate,
      s.completion.k,
      s.completion.n,
      s.p50,
      s.p90,
      s.p99,
      s.kept,
      s.bargeIns,
      s.avgStopMs == null ? null : Math.round(s.avgStopMs),
      s.wer,
      s.talk,
      s.costPerCall,
      s.durationP50,
      sourceChipOf(s)?.text ?? null,
    ]
      .map(esc)
      .join(","),
  );
  return [head.join(","), ...lines].join("\n");
}

function download(name, text) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function SourceChip({ stats }) {
  const src = sourceChipOf(stats);
  if (!src)
    return (
      <Typography typography="s3" color="text.disabled">
        {EMPTY}
      </Typography>
    );
  return (
    <CustomTooltip
      show
      size="small"
      title={
        src.preview
          ? "Latency and interruptions are computed from the separate caller and agent tracks of each recording."
          : "The provider's own timing for each turn."
      }
    >
      <Chip
        size="small"
        variant="outlined"
        label={src.text}
        sx={{ height: 20, typography: "s3", "& .MuiChip-label": { px: 0.75 } }}
      />
    </CustomTooltip>
  );
}
SourceChip.propTypes = { stats: PropTypes.object };

function Cell({ id, s, delta, baseline, keptMs }) {
  const sx = { ...NUM_SX, py: 0.5, typography: "s2" };
  const na = (why) => (
    <CustomTooltip show size="small" title={why}>
      <Box component="span" sx={{ color: "text.disabled" }}>
        {EMPTY}
      </Box>
    </CustomTooltip>
  );
  const textOnly = "Text only · no audio, so no latency";
  switch (id) {
    case "run":
      return null;
    case "completion":
      return (
        <TableCell sx={sx}>
          {s.completion.text}
          {delta != null && !baseline ? (
            <Typography
              component="span"
              typography="s3"
              color={
                delta < 0
                  ? "error.main"
                  : delta > 0
                    ? "success.dark"
                    : "text.secondary"
              }
              sx={{ ml: 0.75 }}
            >
              {fmtPts(delta)}
            </Typography>
          ) : null}
        </TableCell>
      );
    case "p50":
      return (
        <TableCell sx={sx}>
          {s.textOnly ? na(textOnly) : fmtLatency(s.p50)}
        </TableCell>
      );
    case "p90":
      return (
        <TableCell sx={sx}>
          {s.textOnly ? na(textOnly) : fmtLatency(s.p90)}
        </TableCell>
      );
    case "p99":
      return (
        <TableCell sx={sx}>
          {s.textOnly ? na(textOnly) : fmtLatency(s.p99)}
        </TableCell>
      );
    case "kept":
      return (
        <TableCell sx={sx}>
          {s.textOnly ? (
            na("Text only · no audio, so nobody talks over anybody")
          ) : (
            <CustomTooltip
              show
              size="small"
              title={`${s.kept} of ${fmtCount(s.bargeIns, "barge-in")}: the agent kept talking ${fmtMs(keptMs)} or more · average stop ${fmtLatency(s.avgStopMs)}`}
            >
              <span>{`${s.kept} · avg stop ${fmtLatency(s.avgStopMs)}`}</span>
            </CustomTooltip>
          )}
        </TableCell>
      );
    case "wer":
      return (
        <TableCell sx={sx}>
          {s.textOnly ? (
            na("Text only · no speech recognition")
          ) : (
            <CustomTooltip
              show
              size="small"
              title={`${s.werParts.edits} word edits of ${fmtCount(s.werParts.words, "scripted word")}`}
            >
              <span>{fmtPct(s.wer)}</span>
            </CustomTooltip>
          )}
        </TableCell>
      );
    case "talk":
      return (
        <TableCell sx={sx}>
          {s.textOnly
            ? na("Text only · no audio")
            : `${fmtPct(s.talk, { dp: 0 })} agent`}
        </TableCell>
      );
    case "cost":
      return (
        <TableCell sx={sx}>
          <CustomTooltip
            show
            size="small"
            title={costSplitText(s) || "No split recorded"}
          >
            <span>{fmtCost(s.costPerCall)}</span>
          </CustomTooltip>
        </TableCell>
      );
    case "duration":
      return (
        <TableCell sx={sx}>
          {s.textOnly ? na("Text only · no audio") : fmtLatency(s.durationP50)}
        </TableCell>
      );
    case "source":
      return (
        <TableCell sx={{ py: 0.5 }}>
          <SourceChip stats={s} />
        </TableCell>
      );
    default:
      return <TableCell />;
  }
}
Cell.propTypes = {
  id: PropTypes.string,
  s: PropTypes.object,
  delta: PropTypes.number,
  baseline: PropTypes.bool,
  keptMs: PropTypes.number,
};

/**
 * @param {{datasetId: string, runIds: string[], baselineId?: string, scopeId?: string}} props
 */
export default function VoiceSummarySection({
  datasetId,
  runIds,
  baselineId,
  scopeId,
}) {
  const { data: summary } = useVoiceSummary(datasetId, {
    runIds,
    baselineId,
    enabled: Boolean(datasetId) && runIds.length > 0,
  });
  const colors = useRunColors(runIds, baselineId);
  const layout = useColumnLayout({
    surface: SURFACE,
    scopeId: `voice.${scopeId ?? datasetId}`,
    columns: VOICE_COLUMNS,
    commandLabel: "Speed and correctness columns…",
  });
  const [menu, setMenu] = useState(null);
  const [openAll, setOpenAll] = useState(false);
  const model = useMemo(() => {
    if (!summary?.voice) return null;
    const runs = voiceRuns(summary).filter((r) => runIds.includes(r.run_id));
    if (!runs.length) return null;
    const stats = runs.map((r) => voiceRunStats(r, summary));
    const base =
      baselineId && stats.find((s) => s.runId === baselineId)
        ? baselineId
        : stats[0].runId;
    const matrix = voiceMatrix(summary);
    const deltas = Object.fromEntries(
      stats.map((s) => [
        s.runId,
        s.runId === base
          ? null
          : compareRuns({
              matrix,
              baselineId: base,
              candidateId: s.runId,
              noun: "call",
            }).deltaPts,
      ]),
    );
    const voiceStats = stats.filter((s) => !s.textOnly);
    const textStats = stats.filter((s) => s.textOnly);
    const lines = voiceVerdictList(summary, {
      baselineId: base,
      candidateIds: stats
        .filter((s) => s.runId !== base)
        .sort((a, b) => Number(a.textOnly) - Number(b.textOnly))
        .map((s) => s.runId),
    });
    const ids = stats.map((s) => s.runId);
    const personas = personaGroups(summary, {
      runIds: ids,
      baselineId: base,
    }).map((g) => ({
      ...g,
      deltas: Object.fromEntries(
        ids.map((id) => [
          id,
          id === base
            ? null
            : compareRuns({
                matrix,
                baselineId: base,
                candidateId: id,
                scope: g.rowIds,
                noun: "call",
              }).deltaPts,
        ]),
      ),
    }));
    return {
      stats,
      base,
      deltas,
      voiceStats,
      textStats,
      lines,
      personas,
      runs,
      keptMs: summary.thresholds?.kept_talking_ms,
    };
  }, [summary, runIds, baselineId]);
  if (!model) return null;
  const shownLines = openAll ? model.lines : model.lines.slice(0, 3);
  const scale = Math.max(
    ...model.voiceStats.map((s) =>
      s.stages ? s.stages.stt + s.stages.llm + s.stages.tts : 0,
    ),
  );
  const visible = layout.visibleIds.filter((id) => id !== "run");
  const head = (
    <TableHead>
      <TableRow>
        <TableCell
          sx={{
            typography: "s3",
            color: "text.secondary",
            py: 0.5,
            minWidth: 180,
          }}
        >
          Run
        </TableCell>
        {visible.map((id) => (
          <TableCell
            key={id}
            sx={{
              typography: "s3",
              color: "text.secondary",
              py: 0.5,
              textAlign: id === "source" ? "left" : "right",
              whiteSpace: "nowrap",
            }}
          >
            {HEAD[id]}
          </TableCell>
        ))}
      </TableRow>
    </TableHead>
  );
  const runRow = (s, { textGroup } = {}) => (
    <TableRow key={s.runId} data-testid="voice-run-row">
      <TableCell sx={{ py: 0.5 }}>
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ minWidth: 0 }}
        >
          <Box
            sx={{
              width: 3,
              height: 16,
              borderRadius: 1,
              bgcolor: colors[s.runId]?.solid ?? "divider",
              flexShrink: 0,
            }}
          />
          <Typography typography="s2" fontWeight="fontWeightSemiBold" noWrap>
            {s.label}
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0 }}
          >
            {s.fullName}
          </Typography>
          {s.runId === model.base ? (
            <Chip
              size="small"
              label="Baseline"
              sx={{ height: 18, typography: "s3" }}
            />
          ) : null}
        </Stack>
      </TableCell>
      {visible.map((id) =>
        textGroup && id !== "completion" && id !== "cost" ? (
          <TableCell
            key={id}
            sx={{ ...NUM_SX, py: 0.5, color: "text.disabled" }}
          >
            {id === "source" ? "" : EMPTY}
          </TableCell>
        ) : (
          <Cell
            key={id}
            id={id}
            s={s}
            delta={model.deltas[s.runId]}
            baseline={s.runId === model.base}
            keptMs={model.keptMs}
          />
        ),
      )}
    </TableRow>
  );
  const csv = () =>
    download(
      `speed-and-correctness-${datasetId.slice(-4)}.csv`,
      voiceCsv(model.stats),
    );

  return (
    <Box
      id="summary-section-voice"
      data-testid="voice-summary"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 1.5,
        py: 1,
      }}
    >
      <Section
        id="voice"
        surface={SURFACE}
        scopeId={scopeId}
        title="Speed and correctness"
        summary={model.lines[0]?.text}
        actions={
          <Stack direction="row" alignItems="center" gap={0.5}>
            <ColumnsMenu layout={layout} />
            <IconButton
              size="small"
              aria-label="More: download CSV"
              onClick={(e) => setMenu(e.currentTarget)}
            >
              <Iconify icon="mdi:dots-horizontal" width={18} />
            </IconButton>
            <Menu
              anchorEl={menu}
              open={Boolean(menu)}
              onClose={() => setMenu(null)}
            >
              <MenuItem
                sx={{ typography: "s2" }}
                onClick={() => {
                  setMenu(null);
                  csv();
                }}
              >
                <Iconify
                  icon="mdi:download-outline"
                  width={16}
                  sx={{ mr: 1 }}
                />
                Download CSV (every column)
              </MenuItem>
            </Menu>
          </Stack>
        }
      >
        <Stack gap={1.25} sx={{ pt: 0.5 }}>
          <Stack gap={0.5} data-testid="voice-verdicts">
            {shownLines.map((l) => (
              <VerdictLine
                key={l.key}
                compact
                verdict={{
                  tone: l.tone,
                  sentence: l.text,
                  parts: [{ t: "text", text: l.text }],
                  chips: [],
                }}
              />
            ))}
            {model.lines.length > 3 ? (
              <Button
                size="small"
                color="inherit"
                onClick={() => setOpenAll(!openAll)}
                sx={{
                  alignSelf: "flex-start",
                  typography: "s2",
                  color: "text.secondary",
                  ml: 2.5,
                }}
              >
                {openAll ? "Show less" : `+${model.lines.length - 3} more`}
              </Button>
            ) : null}
          </Stack>
          <Box sx={{ overflowX: "auto" }}>
            <Table
              size="small"
              sx={{ "& td, & th": { borderColor: "divider" } }}
            >
              {head}
              <TableBody>
                {model.voiceStats.map((s) => runRow(s))}
                {model.textStats.length ? (
                  <>
                    <TableRow>
                      <TableCell
                        colSpan={visible.length + 1}
                        sx={{ py: 0.5, bgcolor: "background.neutral" }}
                      >
                        <Typography typography="s3" color="text.secondary">
                          Text only · not comparable with voice runs (task
                          completion only)
                        </Typography>
                      </TableCell>
                    </TableRow>
                    {model.textStats.map((s) => runRow(s, { textGroup: true }))}
                  </>
                ) : null}
              </TableBody>
            </Table>
          </Box>
          {model.voiceStats.some((s) => s.stages) ? (
            <Section
              id="voice-stages"
              surface={SURFACE}
              scopeId={scopeId}
              title="Where the time goes"
              defaultOpen={false}
              headingLevel={4}
              summary={stageLine([
                ...model.voiceStats.filter((s) => s.runId !== model.base),
                ...model.voiceStats.filter((s) => s.runId === model.base),
              ])}
            >
              <Stack gap={0.75} sx={{ py: 0.5 }}>
                {model.voiceStats.map((s) => (
                  <Stack
                    key={s.runId}
                    direction="row"
                    alignItems="center"
                    gap={1}
                  >
                    <Typography
                      typography="s2"
                      fontWeight="fontWeightMedium"
                      sx={{ width: 48, flexShrink: 0 }}
                    >
                      {s.label}
                    </Typography>
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <StageSplitBar
                        stages={s.stages}
                        color={colors[s.runId]?.solid ?? "#888"}
                        scaleMs={scale}
                        label={s.label}
                      />
                    </Box>
                  </Stack>
                ))}
                <Typography typography="s3" color="text.secondary">
                  STT · LLM · TTS p50 over every agent turn; the three add up to
                  the time the caller waits.
                </Typography>
              </Stack>
            </Section>
          ) : null}
          <Box>
            <Typography
              typography="s2"
              fontWeight="fontWeightSemiBold"
              sx={{ mb: 0.5 }}
            >
              By persona
            </Typography>
            <Table
              size="small"
              sx={{ "& td, & th": { borderColor: "divider" }, maxWidth: 640 }}
            >
              <TableHead>
                <TableRow>
                  <TableCell
                    sx={{ typography: "s3", color: "text.secondary", py: 0.5 }}
                  >
                    Persona
                  </TableCell>
                  {model.stats.map((s) => (
                    <TableCell
                      key={s.runId}
                      sx={{
                        ...NUM_SX,
                        typography: "s3",
                        color: "text.secondary",
                        py: 0.5,
                      }}
                    >
                      {s.label}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {model.personas.map((g) => (
                  <TableRow key={g.key} data-testid="voice-persona-row">
                    <TableCell sx={{ py: 0.5, typography: "s2" }}>
                      {g.label}
                    </TableCell>
                    {model.stats.map((s) => {
                      const r = g.perRun[s.runId];
                      const d = g.deltas[s.runId];
                      return (
                        <TableCell
                          key={s.runId}
                          sx={{ ...NUM_SX, py: 0.5, typography: "s2" }}
                        >
                          {r?.n ? `${r.k}/${r.n}` : EMPTY}
                          {d != null && d !== 0 ? (
                            <Typography
                              component="span"
                              typography="s3"
                              color={d < 0 ? "error.main" : "success.dark"}
                              sx={{ ml: 0.5 }}
                            >
                              {`${fmtPts(d)}${g.worse[s.runId] ? " ▼" : ""}`}
                            </Typography>
                          ) : null}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
          <Stack gap={0.25}>
            {model.voiceStats.map((s) => {
              const t = sourcesText(summary, s.runId);
              return t ? (
                <Typography
                  key={s.runId}
                  typography="s3"
                  color="text.secondary"
                  data-testid="voice-sources"
                >
                  {`${s.label}: `}
                  {t}
                </Typography>
              ) : null;
            })}
          </Stack>
        </Stack>
      </Section>
    </Box>
  );
}

VoiceSummarySection.propTypes = {
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  scopeId: PropTypes.string,
};
