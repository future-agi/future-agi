import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import {
  Box, Stack, Typography, IconButton, Tooltip, Tab, TextField,
  InputAdornment, Switch, Collapse, Button,
} from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { SegmentedTabs, CustomTabs } from "src/components/tabs/tabs";
import SideDrawer from "../components/SideDrawer";
import { getSurface } from "../_mock/surfaces";
import { hashSeed } from "../_mock/runStream";
import {
  callDetail, callDeltas, transcriptDiff, diffTally, graphDiff,
} from "../_mock/callDetail";
import { DomainChip } from "../components/primitives";
import CallGraph, { GraphLegend } from "./CallGraph";

/**
 * One scenario, every run, in full.
 *
 * The comparison table shows a single line per run — the thing that decided the
 * verdict — because a wall of transcripts is not something anyone reads. This
 * is where that restraint is paid back: when the one line is not enough, every
 * run's call is here in a column of its own, and the columns are aligned so the
 * eye can do the comparing.
 *
 * Four artifacts per column, because a call fails in four different ways and
 * each one has its own evidence:
 *
 *   numbers      where the seconds and the cents went
 *   transcript   what was said, and where the dead air was
 *   checklist    which steps of the reference path were actually done
 *   graph        the route taken — including the exit taken instead
 *
 * With Show Diff on, every one of those is stated against the baseline rather
 * than on its own. That is the difference between "this run scored 55%" and
 * "this run stopped talking two steps earlier than the one you shipped".
 */

const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

const ROLE_LABEL = { agent: "ASSISTANT", customer: "USER" };

export default function CallCompare({
  open, row, rows = [], runs = [], env, envState, diff, onDiff, onClose, onOpenRow,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const [tab, setTab] = useState("transcript");
  const [wide, setWide] = useState(false);

  const surface = getSurface(env?.surface);
  const voice = surface.stage === "voice";

  /*
    Built for every column at once so the columns can be read against each
    other — a delta chip on column C is meaningless without column A's numbers
    in the same object.
  */
  const details = useMemo(() => {
    if (!row) return [];
    return row.cells.map((cell) => {
      const run = runs.find((r) => r.id === cell.runId);
      return callDetail({ env, envState, run, task: cell.task, scenario: row });
    });
  }, [row, runs, env, envState]);

  if (!row) return null;

  const baseline = details[0];
  const index = rows.findIndex((r) => r.id === row.id);
  const go = (step) => {
    const next = rows[index + step];
    if (next) onOpenRow(next);
  };

  return (
    /*
      The same drawer as everywhere else in here — right-anchored, transparent
      backdrop, one surface colour. A panel that dims the page behind it says
      "stop looking at that", and this one is for acting on the table you were
      just reading: the list of scenarios stays visible and legible beside it.
    */
    <SideDrawer
      open={open}
      onClose={onClose}
      width={wide ? "100vw" : { xs: "100vw", md: "calc(100vw - 220px)" }}
    >
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        {/* ── identity and the things you do to the whole panel ── */}
        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Typography sx={{ typography: "s2", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace" }}>
            Call ID : <Box component="span" sx={{ color: "text.primary" }}>{row.id}</Box>
          </Typography>
          <Tooltip arrow title="Copy scenario id">
            <IconButton
              size="small"
              onClick={() => {
                navigator.clipboard?.writeText(row.id);
                enqueueSnackbar("Scenario id copied", { variant: "success" });
              }}
            >
              <Iconify icon="solar:copy-linear" width={14} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>

          <Box flex={1} />

          {/* Moving through the table without closing the panel: the whole
              point of opening a row is to compare it with the next one. */}
          <Tooltip arrow title={rows[index - 1] ? `Previous — ${rows[index - 1].title}` : "First scenario"}>
            <span>
              <IconButton size="small" disabled={!rows[index - 1]} onClick={() => go(-1)}>
                <Iconify icon="eva:arrow-ios-upward-fill" width={16} sx={{ color: "text.subtitle" }} />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip arrow title={rows[index + 1] ? `Next — ${rows[index + 1].title}` : "Last scenario"}>
            <span>
              <IconButton size="small" disabled={!rows[index + 1]} onClick={() => go(1)}>
                <Iconify icon="eva:arrow-ios-downward-fill" width={16} sx={{ color: "text.subtitle" }} />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip arrow title={wide ? "Exit full width" : "Full width"}>
            <IconButton size="small" onClick={() => setWide((w) => !w)}>
              <Iconify
                icon={wide ? "solar:minimize-square-minimalistic-linear" : "solar:maximize-square-minimalistic-linear"}
                width={15}
                sx={{ color: "text.subtitle" }}
              />
            </IconButton>
          </Tooltip>
          <Tooltip arrow title="Copy link to this scenario">
            <IconButton
              size="small"
              onClick={() => {
                navigator.clipboard?.writeText(`${window.location.href}#${row.id}`);
                enqueueSnackbar("Link copied", { variant: "success" });
              }}
            >
              <Iconify icon="solar:share-linear" width={15} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
          <Tooltip arrow title="Close">
            <IconButton size="small" onClick={onClose}>
              <Iconify icon="mingcute:close-line" width={16} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
        </Stack>

        {/* ── which surface this was, and what it is being read against ── */}
        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{ px: 2, pt: 1.25, pb: 1, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Iconify icon={surface.icon} width={15} sx={{ color: "primary.main" }} />
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "primary.main" }}>
            {surface.label}
          </Typography>
          <Box sx={{ width: "1px", height: 14, bgcolor: "divider", mx: 1 }} />
          <Typography noWrap sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
            {row.title}
          </Typography>
        </Stack>

        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Iconify icon="solar:copy-linear" width={14} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
            Comparing {row.cells.length} runs
          </Typography>
          <Box flex={1} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>Show Diff</Typography>
          <Switch size="small" checked={!!diff} onChange={(e) => onDiff?.(e.target.checked)} />
        </Stack>

        {/* ── the columns ── */}
        <Stack direction="row" sx={{ flex: 1, minHeight: 0, overflowX: "auto" }}>
          {row.cells.map((cell, i) => (
            <RunColumn
              key={cell.runId}
              cell={cell}
              run={runs.find((r) => r.id === cell.runId)}
              detail={details[i]}
              baseline={i === 0 ? null : baseline}
              isBaseline={i === 0}
              voice={voice}
              diff={diff}
              tab={tab}
              onTab={setTab}
            />
          ))}
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

CallCompare.propTypes = {
  open: PropTypes.bool,
  row: PropTypes.object,
  rows: PropTypes.array,
  runs: PropTypes.array,
  env: PropTypes.object,
  envState: PropTypes.object,
  diff: PropTypes.bool,
  onDiff: PropTypes.func,
  onClose: PropTypes.func,
  onOpenRow: PropTypes.func,
};

/* ── one run's column ────────────────────────────────────────────────────── */

function RunColumn({ cell, run, detail, baseline, isBaseline, voice, diff, tab, onTab }) {
  const [summaryOpen, setSummaryOpen] = useState(false);

  return (
    <Stack
      sx={{
        flex: 1, minWidth: 430,
        borderRight: "1px solid", borderColor: "divider",
        "&:last-of-type": { borderRight: "none" },
      }}
    >
      {/* who this column is */}
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Box
          sx={{
            width: 20, height: 20, borderRadius: 0.75, flexShrink: 0,
            display: "grid", placeItems: "center", typography: "s3", fontWeight: 700,
            color: cell.color,
            bgcolor: (t) => alpha(cell.color, t.palette.mode === "dark" ? 0.2 : 0.12),
          }}
        >
          {cell.letter}
        </Box>
        <Typography noWrap sx={{ typography: "s1", fontWeight: 700, flex: 1, minWidth: 0 }}>
          Run {run?.ordinal} · agent {run?.agentVersion}
        </Typography>
        {detail && !detail.measured && <DomainChip domain={detail.domain} />}
        {isBaseline && (
          <Typography
            sx={{
              px: 0.875, py: 0.25, borderRadius: 0.5, flexShrink: 0,
              typography: "s3", fontWeight: 700, color: "primary.main",
              bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.2 : 0.12),
            }}
          >
            Baseline
          </Typography>
        )}
      </Stack>

      {!detail ? (
        <Box sx={{ p: 3 }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            This scenario did not run here, so there is nothing to compare against the other columns.
          </Typography>
        </Box>
      ) : (
        <Box key={detail.id} sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          {/*
            Attribution before anything else. Every number below this describes
            a run the agent never got a fair shot at, and reading them as agent
            performance is exactly the mistake the taxonomy exists to stop.
          */}
          {!detail.measured && (
            <Stack
              direction="row" alignItems="flex-start" spacing={1}
              sx={{
                mx: 2, mt: 1.25, p: 1.25, borderRadius: 1, border: "1px solid",
                borderColor: (t) => alpha(detail.domain.color, 0.4),
                bgcolor: (t) => alpha(detail.domain.color, t.palette.mode === "dark" ? 0.12 : 0.06),
              }}
            >
              <Iconify icon="solar:shield-cross-bold" width={15} sx={{ color: detail.domain.color, flexShrink: 0, mt: "1px" }} />
              <Box minWidth={0}>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Typography sx={{ typography: "s2", fontWeight: 700, color: detail.domain.color }}>
                    {detail.domain.label} — not measured
                  </Typography>
                </Stack>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>{detail.fault}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
                  {detail.domain.retry} {detail.domain.next}
                </Typography>
              </Box>
            </Stack>
          )}

          {/* ── the call's own facts ── */}
          <Stack direction="row" flexWrap="wrap" rowGap={0.75} spacing={0.75} sx={{ px: 2, py: 1.25 }}>
            <Meta label="Type" value={detail.type} />
            <Meta label="Status" value={detail.outcome.label} color={detail.outcome.color} />
            <Meta label="Duration" value={`0m ${Math.round(detail.durationS)}s`} />
            <Meta label="Avg Latency" value={`${(detail.latencyMs / 1000).toFixed(1)}s`} />
          </Stack>
          <Stack direction="row" flexWrap="wrap" rowGap={0.75} spacing={0.75} sx={{ px: 2, pb: 1.25 }}>
            <Meta label="Phone" value={detail.phone} mono />
            <Meta label="Provider" value={detail.provider} />
          </Stack>

          {/*
            Against the baseline, in the header, because these four numbers are
            the ones someone quotes in a release thread — and quoting them
            without the comparison is how "we cut latency" gets said about a run
            that was 300ms slower.
          */}
          {/* The baseline keeps the row rather than skipping it: columns that are
              meant to be read across have to start their numbers on the same
              line, and a row that appears in two of three columns shifts every
              one of them out of alignment. */}
          {diff && isBaseline && (
            <Stack direction="row" alignItems="center" spacing={0.5} sx={{ px: 2, pb: 1.25, minHeight: 34 }}>
              <Iconify icon="solar:flag-linear" width={13} sx={{ color: "text.subtitle" }} />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Everything to the right is read against this run.
              </Typography>
            </Stack>
          )}

          {/* Diff on, but the baseline never ran this scenario: there is no
              comparison to make, and drawing the panel as if there were one
              would imply this run changed something. */}
          {diff && !isBaseline && !baseline && (
            <Stack direction="row" alignItems="center" spacing={0.5} sx={{ px: 2, pb: 1.25, minHeight: 34 }}>
              <Iconify icon="solar:info-circle-linear" width={13} sx={{ color: "text.subtitle" }} />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                The baseline run never covered this scenario, so there is nothing to diff against.
              </Typography>
            </Stack>
          )}

          {diff && baseline && (
            <Stack
              direction="row" alignItems="center" flexWrap="wrap" rowGap={0.75} spacing={0.75}
              sx={{ px: 2, pb: 1.25, minHeight: 34 }}
            >
              <Stack direction="row" alignItems="center" spacing={0.5} sx={{ flexShrink: 0 }}>
                <Iconify icon="solar:transfer-horizontal-linear" width={13} sx={{ color: "text.subtitle" }} />
                <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.3 }}>
                  Δ baseline
                </Typography>
              </Stack>
              {callDeltas(detail, baseline).map((d) => (
                <DeltaChip key={d.id} delta={d} />
              ))}
              {callDeltas(detail, baseline).length === 0 && (
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>identical on every measure</Typography>
              )}
            </Stack>
          )}

          {/* ── the numbers ── */}
          <Box
            sx={{
              mx: 2, mb: 1.5,
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              border: "1px solid", borderColor: "divider", borderRadius: 1,
              overflow: "hidden",
            }}
          >
            <Stat label="Duration" value={`${detail.durationS}s`} />
            <Stat label="Turns" value={detail.stats.turnCount} />
            <Stat label="Latency" value={`${detail.latencyMs}ms`} />
            <Stat
              label="User / AI"
              value={(
                <>
                  <Box component="span" sx={{ color: "#EA580C" }}>{detail.stats.userPct}</Box>
                  <Box component="span" sx={{ color: "text.disabled" }}> / </Box>
                  <Box component="span" sx={{ color: "#7857FC" }}>{detail.stats.aiPct}</Box>
                </>
              )}
            />
            {/* The environment's payout for this episode, next to the numbers
                it was computed from. Hover for the terms — a return nobody can
                decompose is a score nobody can argue with, and arguing with it
                is how a reward spec gets fixed. */}
            <Stat
              label="Return"
              value={detail.return == null ? "—" : detail.return.total.toFixed(2)}
              hint={detail.return
                ? `${detail.return.terms.map((t) => `${t.label} ${t.value > 0 ? "+" : ""}${t.value.toFixed(2)}`).join(" · ")} — ${detail.rewardSpec}`
                : "No verdict, so no return. Zero would be what an agent scores by doing nothing."}
            />
            <Stat label="Words" value={detail.stats.words} />
            <Stat label="Silence" value={voice ? `${detail.stats.silenceS.toFixed(2)}s` : "—"} />
            <Stat label="TTFW" value={`${detail.stats.ttfwMs}ms`} />
            <Stat label="User int." value={detail.stats.userInt} />
            <Stat label="AI int." value={detail.stats.aiInt} />
          </Box>

          {/* ── where the seconds went ── */}
          {voice && (
            <Box sx={{ px: 2, pb: 1.5 }}>
              <SectionLabel>
                Latency pipeline
                <Box component="span" sx={{ float: "right", color: "text.secondary", fontWeight: 600 }}>
                  Total {(detail.latencyMs / 1000).toFixed(2)}s
                </Box>
              </SectionLabel>
              <Stack direction="row" sx={{ height: 20, borderRadius: 0.75, overflow: "hidden", mt: 0.75 }}>
                {detail.latency.map((l) => (
                  <Box
                    key={l.id}
                    sx={{
                      width: `${(l.ms / detail.latencyMs) * 100}%`,
                      bgcolor: l.color,
                      display: "grid", placeItems: "center",
                      borderRight: "1px solid", borderColor: "background.paper",
                    }}
                  >
                    <Typography noWrap sx={{ typography: "s3", fontWeight: 700, color: "#fff", px: 0.5 }}>
                      {l.ms}ms
                    </Typography>
                  </Box>
                ))}
              </Stack>
              <Stack direction="row" flexWrap="wrap" rowGap={0.5} spacing={1.5} sx={{ mt: 0.75 }}>
                {detail.latency.map((l) => (
                  <Stack key={l.id} direction="row" alignItems="center" spacing={0.5}>
                    <Box sx={{ width: 7, height: 7, borderRadius: 0.25, bgcolor: l.color, flexShrink: 0 }} />
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      {l.label} <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>{l.ms}ms</Box>
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </Box>
          )}

          {/* ── where the money went ── */}
          <Box sx={{ px: 2, pb: 1.5 }}>
            <SectionLabel>
              Cost breakdown
              <Box component="span" sx={{ float: "right", color: "text.secondary", fontWeight: 600 }}>
                Total ${detail.costTotal.toFixed(3)}
              </Box>
            </SectionLabel>
            <Stack spacing={0.25} sx={{ mt: 0.5 }}>
              {detail.cost.map((c) => (
                <Stack key={c.id} direction="row" alignItems="center" spacing={1.25} sx={{ py: 0.75 }}>
                  <Iconify icon={c.icon} width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                  <Typography noWrap sx={{ typography: "s2", width: 108, flexShrink: 0 }}>{c.label}</Typography>
                  <Box sx={{ flex: 1, minWidth: 0, height: 4, borderRadius: 2, bgcolor: "background.neutral" }}>
                    <Box sx={{ width: `${c.pct}%`, height: 1, borderRadius: 2, bgcolor: "#2563EB" }} />
                  </Box>
                  <Box sx={{ width: 62, flexShrink: 0, textAlign: "right" }}>
                    <Typography sx={{ typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                      ${c.amount.toFixed(3)}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{c.pct}%</Typography>
                  </Box>
                </Stack>
              ))}
            </Stack>
          </Box>

          {/* ── the one-line read ── */}
          <Box sx={{ px: 2, pb: 1.5 }}>
            <SectionLabel>AI summary</SectionLabel>
            <Stack
              direction="row" alignItems="flex-start" spacing={1}
              onClick={() => setSummaryOpen((o) => !o)}
              sx={{
                mt: 0.5, px: 1.25, py: 1, borderRadius: 1, cursor: "pointer",
                border: "1px solid", borderColor: "divider",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Iconify icon="solar:magic-stick-3-linear" width={14} sx={{ color: "#7857FC", flexShrink: 0, mt: "2px" }} />
              <Typography
                sx={{
                  typography: "s2", color: "text.secondary", flex: 1, minWidth: 0,
                  ...(summaryOpen ? {} : { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }),
                }}
              >
                {detail.summary}
              </Typography>
              <Iconify
                icon={summaryOpen ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
                width={14}
                sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }}
              />
            </Stack>
          </Box>

          {/* ── the artifacts ── */}
          <CustomTabs
            value={tab}
            onChange={(_, v) => onTab(v)}
            sx={{ px: 1, borderBottom: "1px solid", borderColor: "divider", minHeight: 40 }}
          >
            <Tab
              value="transcript" sx={{ minHeight: 40 }}
              icon={<Iconify icon="solar:document-text-linear" width={14} />}
              iconPosition="start" label="Transcript"
            />
            <Tab
              value="checklist" sx={{ minHeight: 40 }}
              icon={<Iconify icon="solar:checklist-minimalistic-linear" width={14} />}
              iconPosition="start" label="Checklist"
            />
            <Tab
              value="graph" sx={{ minHeight: 40 }}
              icon={<Iconify icon="solar:routing-2-linear" width={14} />}
              iconPosition="start" label="Graph"
            />
          </CustomTabs>

          {tab === "transcript" && (
            <TranscriptPane detail={detail} baseline={baseline} diff={diff} voice={voice} />
          )}
          {tab === "checklist" && <ChecklistPane detail={detail} />}
          {tab === "graph" && <GraphPane detail={detail} baseline={baseline} diff={diff} onTab={onTab} />}
        </Box>
      )}
    </Stack>
  );
}

RunColumn.propTypes = {
  cell: PropTypes.object, run: PropTypes.object, detail: PropTypes.object,
  baseline: PropTypes.object, isBaseline: PropTypes.bool, voice: PropTypes.bool,
  diff: PropTypes.bool, tab: PropTypes.string, onTab: PropTypes.func,
};

/* ── transcript ──────────────────────────────────────────────────────────── */

function TranscriptPane({ detail, baseline, diff, voice }) {
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("all");

  /* With a baseline, a turn is not read on its own — it is read as what this
     version says where the shipped one said something else. */
  const showDiff = diff && !!baseline;
  const rows = useMemo(
    () => (showDiff ? transcriptDiff(detail.turns, baseline.turns) : detail.turns.map((t) => ({ ...t, diff: "same" }))),
    [showDiff, detail.turns, baseline],
  );
  const tally = diffTally(rows);

  const shown = rows.filter((t) => {
    if (role !== "all" && t.role !== role) return false;
    if (query && !t.text.toLowerCase().includes(query.toLowerCase())) return false;
    return true;
  });

  return (
    <Box sx={{ pb: 2 }}>
      {voice && <Recording detail={detail} />}

      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1.25 }}>
        <TextField
          size="small" fullWidth placeholder="Search transcript"
          value={query} onChange={(e) => setQuery(e.target.value)}
          InputProps={{
            sx: { typography: "s2" },
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: "text.subtitle" }} />
              </InputAdornment>
            ),
          }}
        />
        <SegmentedTabs value={role} onChange={(_, v) => setRole(v)} sx={{ flexShrink: 0 }}>
          <Tab value="all" label="All" />
          <Tab value="agent" label="Assistant" />
          <Tab value="customer" label="Customer" />
        </SegmentedTabs>
      </Stack>

      {showDiff && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, pb: 1 }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
            Transcript diff
          </Typography>
          <Count n={tally.changed} label="changed" color="#B98A3C" />
          <Count n={tally.added} label="added" color="#5AA47B" />
        </Stack>
      )}

      <Stack sx={{ px: 2 }}>
        {shown.map((t, i) => (
          <React.Fragment key={t.id}>
            <Turn turn={t} showDiff={showDiff} />
            {voice && t.silenceAfter >= 1 && i < shown.length - 1 && (
              <Stack direction="row" alignItems="center" spacing={0.75} sx={{ px: 1.5, py: 0.5 }}>
                <Iconify icon="solar:hourglass-line-linear" width={11} sx={{ color: "text.disabled" }} />
                <Typography sx={{ typography: "s3", color: "text.disabled", fontFamily: "ui-monospace, Menlo, monospace" }}>
                  {t.silenceAfter.toFixed(1)}s silence
                </Typography>
                <Box sx={{ flex: 1, borderBottom: "1px dashed", borderColor: "divider" }} />
              </Stack>
            )}
          </React.Fragment>
        ))}
        {shown.length === 0 && (
          <Typography sx={{ typography: "s2", color: "text.subtitle", py: 2 }}>
            Nothing in this transcript matches.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}

TranscriptPane.propTypes = {
  detail: PropTypes.object, baseline: PropTypes.object, diff: PropTypes.bool, voice: PropTypes.bool,
};

function Turn({ turn, showDiff }) {
  const changed = showDiff && turn.diff === "changed";
  const added = showDiff && turn.diff === "added";
  const accent = changed ? "#B98A3C" : added ? "#5AA47B" : null;

  return (
    <Box
      sx={{
        borderLeft: "2px solid",
        borderColor: (t) => (accent || (turn.role === "agent" ? t.palette.primary.main : t.palette.text.disabled)),
        bgcolor: (t) => (accent
          ? alpha(accent, t.palette.mode === "dark" ? 0.1 : 0.06)
          : turn.role === "agent"
            ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.06 : 0.04)
            : "background.neutral"),
        px: 1.5, py: 1, mb: 0.5,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace" }}>
          {mmss(turn.at)}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.disabled", fontFamily: "ui-monospace, Menlo, monospace" }}>
          {turn.dur.toFixed(1)}s
        </Typography>
        {showDiff && (
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle" }}>
            {ROLE_LABEL[turn.role]}
          </Typography>
        )}
        {accent && (
          <Typography
            sx={{
              px: 0.625, borderRadius: 0.5, typography: "s3", fontWeight: 700, color: accent,
              bgcolor: (t) => alpha(accent, t.palette.mode === "dark" ? 0.2 : 0.12),
            }}
          >
            {changed ? "Changed" : "Added"}
          </Typography>
        )}
      </Stack>

      {/* What the baseline said here, kept above what this run said — the pair
          is the finding, and showing only the new line hides it. */}
      {changed && (
        <Typography
          sx={{
            typography: "s2", mt: 0.375, color: "text.disabled", textDecoration: "line-through",
          }}
        >
          {turn.was}
        </Typography>
      )}
      <Typography sx={{ typography: "s2", mt: 0.375 }}>{turn.text}</Typography>
    </Box>
  );
}

Turn.propTypes = { turn: PropTypes.object, showDiff: PropTypes.bool };

/* ── checklist ───────────────────────────────────────────────────────────── */

const STEP_TONE = {
  addressed: { color: "#16A34A", icon: "solar:check-circle-bold", label: "Addressed" },
  partial: { color: "#CA8A04", icon: "solar:info-circle-bold", label: "Partial" },
  missed: { color: "#DC2626", icon: "solar:close-circle-bold", label: "Missed" },
};

function ChecklistPane({ detail }) {
  const { steps, pass, partial, missed, pct } = detail.checklist;

  return (
    <Box sx={{ pb: 2 }}>
      <Stack direction="row" alignItems="center" flexWrap="wrap" rowGap={0.5} spacing={1.5} sx={{ px: 2, py: 1.25 }}>
        <Typography
          sx={{
            px: 0.875, py: 0.25, borderRadius: 0.5, typography: "s3", fontWeight: 700,
            color: pct >= 80 ? "#16A34A" : pct >= 40 ? "#CA8A04" : "#DC2626",
            bgcolor: (t) => alpha(pct >= 80 ? "#16A34A" : pct >= 40 ? "#CA8A04" : "#DC2626", t.palette.mode === "dark" ? 0.18 : 0.1),
          }}
        >
          {pct}% · {pass + partial}/{steps.length} steps
        </Typography>
        <Count n={pass} label="pass" color="#16A34A" dot />
        <Count n={partial} label="partial" color="#CA8A04" dot />
        <Count n={missed} label="missed" color="#DC2626" dot />
      </Stack>

      <Stack sx={{ px: 2 }} spacing={1}>
        {steps.map((s) => (
          <ChecklistStep key={s.id} step={s} total={steps.length} />
        ))}
      </Stack>
    </Box>
  );
}

ChecklistPane.propTypes = { detail: PropTypes.object };

function ChecklistStep({ step, total }) {
  const tone = STEP_TONE[step.status];
  /* Anything that did not simply pass opens by default: those are the rows
     someone came here to read, and making them click for the reason adds a
     step to the only interaction that matters. */
  const [open, setOpen] = useState(step.status !== "addressed");

  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
      <Stack
        direction="row" alignItems="center" spacing={1}
        onClick={() => setOpen((o) => !o)}
        sx={{ px: 1.5, py: 1, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
      >
        <Iconify icon={tone.icon} width={15} sx={{ color: tone.color, flexShrink: 0 }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace", flexShrink: 0 }}>
          {String(step.index + 1).padStart(2, "0")}/{String(total).padStart(2, "0")}
        </Typography>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace", flex: 1, minWidth: 0 }}>
          {step.name}
        </Typography>
        <Typography
          sx={{
            px: 0.75, py: 0.125, borderRadius: 0.5, flexShrink: 0,
            typography: "s3", fontWeight: 700, color: tone.color, textTransform: "uppercase",
            bgcolor: (t) => alpha(tone.color, t.palette.mode === "dark" ? 0.18 : 0.1),
          }}
        >
          {tone.label}
        </Typography>
        <Iconify
          icon={open ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
          width={14}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
      </Stack>

      <Collapse in={open}>
        <Box sx={{ px: 1.5, pb: 1.25 }}>
          <Typography sx={{ typography: "s2", fontStyle: "italic", color: "text.secondary" }}>
            &ldquo;{step.expectation}&rdquo;
          </Typography>
          {step.evidence ? (
            <Stack
              direction="row" alignItems="flex-start" spacing={1}
              sx={{
                mt: 0.875, p: 1, borderRadius: 0.75,
                bgcolor: "background.neutral",
              }}
            >
              <Typography sx={{ typography: "s3", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace", flexShrink: 0 }}>
                ▸ {mmss(step.evidence.at)}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
                {step.evidence.text}
              </Typography>
              {/* How sure the grader is, said out loud — a partial with 66%
                  behind it is a different instruction from a partial at 95%. */}
              <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", flexShrink: 0 }}>
                {step.evidence.confidence}%
              </Typography>
            </Stack>
          ) : (
            <Typography sx={{ typography: "s2", color: "text.disabled", mt: 0.5 }}>
              No matching turn — the agent never touched this step.
            </Typography>
          )}
        </Box>
      </Collapse>
    </Box>
  );
}

ChecklistStep.propTypes = { step: PropTypes.object, total: PropTypes.number };

/* ── graph ───────────────────────────────────────────────────────────────── */

function GraphPane({ detail, baseline, diff, onTab }) {
  const showDiff = diff && !!baseline;

  /*
    Every node carries what happened at it. A route with bare step names tells
    you where the run went and nothing about why — and the answer is already a
    tab away in the checklist, so the node points at it rather than repeating
    it.
  */
  const hint = useMemo(() => {
    const byId = new Map(detail.checklist.steps.map((s) => [s.id, s]));
    return (n) => {
      if (n.kind === "baseline") return `${n.label} — the baseline went here; this run did not.`;
      if (n.kind === "skipped") return `${n.label} — this scenario needed it and the agent never called it.`;
      if (n.kind === "alternate") return `${n.label} — the branch the rule could have taken. It did not.`;
      const step = byId.get(n.id);
      if (step) {
        return `${n.label} — ${step.status}${step.evidence ? ` · ${step.evidence.confidence}% confidence` : ""}. Click to open it in the checklist.`;
      }
      return `${n.label} — ${n.sub || ""}`;
    };
  }, [detail]);

  const g = useMemo(
    () => (showDiff
      ? graphDiff(detail.graph, baseline.graph)
      : { spine: detail.graph.spine, branches: detail.graph.branches, added: 0, missingCount: 0 }),
    [showDiff, detail, baseline],
  );

  const spine = g.spine.map((n) => ({ ...n, hint: hint(n) }));
  const branches = g.branches.map((b) => ({
    ...b,
    nodes: b.nodes.map((n) => ({ ...n, hint: hint(n) })),
  }));
  const skipped = detail.graph.branches.filter((b) => b.kind === "skipped").length;

  return (
    <Box sx={{ pb: 2 }}>
      <Stack direction="row" alignItems="center" flexWrap="wrap" rowGap={0.5} spacing={1} sx={{ px: 2, py: 1.25 }}>
        <Iconify icon="solar:routing-2-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
          {showDiff ? "vs baseline" : "trajectory"}
        </Typography>
        {showDiff ? (
          <>
            <Count n={g.added} label="added" color="#5AA47B" prefix="+" />
            <Count n={g.missingCount} label="missing" color="#C2603F" prefix="−" />
          </>
        ) : (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {spine.length} steps · {detail.turns.length} turns
          </Typography>
        )}
        {/* A skipped step is the finding, so it is stated in the header rather
            than left for someone to spot in the drawing. */}
        {skipped > 0 && <Count n={skipped} label={skipped === 1 ? "step skipped" : "steps skipped"} color="#C2603F" />}
      </Stack>

      {/* The route is a way in, not a dead end: a node names a checklist step,
          and clicking it goes there rather than making someone find it. */}
      <CallGraph
        spine={spine}
        branches={branches}
        diff={showDiff}
        onNodeClick={(n) => { if (!["baseline", "alternate"].includes(n.kind)) onTab("checklist"); }}
        sx={{ mx: 2 }}
      />
      <GraphLegend diff={showDiff} />

      {/* The read, under the route it is about. */}
      <Box sx={{ mx: 2, mt: 1, p: 1.5, borderRadius: 1, border: "1px solid", borderColor: "divider" }}>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
          <Iconify icon="solar:magic-stick-3-linear" width={14} sx={{ color: "#7857FC" }} />
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
            Falcon analysis
          </Typography>
        </Stack>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>{detail.analysis}</Typography>
      </Box>
    </Box>
  );
}

GraphPane.propTypes = {
  detail: PropTypes.object, baseline: PropTypes.object, diff: PropTypes.bool, onTab: PropTypes.func,
};

/* ── small pieces ────────────────────────────────────────────────────────── */

function SectionLabel({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3", fontWeight: 700, color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.4, display: "block",
      }}
    >
      {children}
    </Typography>
  );
}
SectionLabel.propTypes = { children: PropTypes.node };

function Meta({ label, value, color, mono }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.625}
      sx={{ px: 0.875, py: 0.375, borderRadius: 0.75, border: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label} :</Typography>
      <Typography
        sx={{
          typography: "s3", fontWeight: 700,
          color: color || "text.primary",
          fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined,
        }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
Meta.propTypes = { label: PropTypes.string, value: PropTypes.node, color: PropTypes.string, mono: PropTypes.bool };

function Stat({ label, value, hint }) {
  const cell = (
    <Box sx={{ px: 1.25, py: 1, borderRight: "1px solid", borderBottom: "1px solid", borderColor: "divider" }}>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.3 }}>
        {label}
      </Typography>
      <Typography sx={{ typography: "m2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
    </Box>
  );
  return hint ? <Tooltip arrow title={hint}>{cell}</Tooltip> : cell;
}
Stat.propTypes = { label: PropTypes.string, value: PropTypes.node, hint: PropTypes.string };

function Count({ n, label, color, dot, prefix }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      {dot && <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: color, flexShrink: 0 }} />}
      <Typography
        sx={{
          typography: "s3", fontWeight: 700, color,
          ...(dot ? {} : {
            px: 0.625, py: 0.125, borderRadius: 0.5,
            bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.18 : 0.1),
          }),
        }}
      >
        {prefix || ""}{n} {label}
      </Typography>
    </Stack>
  );
}
Count.propTypes = {
  n: PropTypes.number, label: PropTypes.string, color: PropTypes.string,
  dot: PropTypes.bool, prefix: PropTypes.string,
};

function DeltaChip({ delta }) {
  const up = delta.value > 0;
  const good = delta.neutral ? null : delta.lowerIsBetter ? !up : up;
  const color = good == null ? "text.subtitle" : good ? "#5AA47B" : "#C2603F";
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.5}
      sx={{ px: 0.875, py: 0.375, borderRadius: 0.75, border: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{delta.label}</Typography>
      <Iconify
        icon={up ? "eva:arrow-upward-fill" : "eva:arrow-downward-fill"}
        width={12}
        sx={{ color }}
      />
      <Typography sx={{ typography: "s3", fontWeight: 700, color }}>
        {delta.format(delta.value)}
      </Typography>
    </Stack>
  );
}
DeltaChip.propTypes = { delta: PropTypes.object };

/**
 * The recording.
 *
 * Two channels, not one strip: a call is two people, and the single question
 * anyone asks of a waveform — who was talking, and when did nobody talk — has
 * no answer in a mixed track. Split by speaker, the shape of the call is
 * legible at a glance: an assistant monologue is a solid block on the bottom
 * row with an empty top row above it, and 23 seconds of dead air is a gap in
 * both.
 *
 * There is no audio in a prototype, so the bars are drawn from the turns that
 * exist — each one sits at its own timestamp, for its own duration. That makes
 * the picture true even though the sound is not there, and the controls say so
 * rather than pretending: playback is disabled, and Download hands over the
 * transcript, which is the artifact we actually have.
 */
const TALK = {
  customer: "#E0913A",
  assistant: (mode) => (mode === "dark" ? "#E4E4E7" : "#52525B"),
};

const CHANNEL_H = 34;

function Recording({ detail }) {
  const theme = useTheme();
  const assistant = TALK.assistant(theme.palette.mode);
  const turns = detail.turns;
  const total = Math.max(1, ...turns.map((t) => t.at + t.dur));

  /* A tick every 30s on a long call, every 10 on a short one — the ruler is
     there to place a turn in the call, not to be read precisely. */
  const step = total > 150 ? 60 : total > 60 ? 30 : total > 25 ? 10 : 5;
  const ticks = [];
  for (let s = 0; s <= total; s += step) ticks.push(s);

  const downloadTranscript = () => {
    const text = turns
      .map((t) => `[${mmss(t.at)}] ${t.role === "agent" ? "Assistant" : "Customer"}: ${t.text}`)
      .join("\n");
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${detail.id}-transcript.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const channel = (role, color) => (
    <Box sx={{ position: "relative", height: CHANNEL_H }}>
      {/* Silence is the baseline showing through — the gaps are the point. */}
      <Box
        sx={{
          position: "absolute", left: 0, right: 0, top: "50%",
          borderTop: "1px dashed", borderColor: "divider",
        }}
      />
      {turns.filter((t) => t.role === role).map((t) => (
        <Stack
          key={t.id}
          direction="row" alignItems="center" justifyContent="space-between"
          sx={{
            position: "absolute", top: 0, bottom: 0,
            left: `${(t.at / total) * 100}%`,
            width: `${Math.max(0.5, (t.dur / total) * 100)}%`,
          }}
        >
          {/* Thin and evenly spread rather than stretched to fill: a bar as
              wide as the gap beside it reads as a bar chart, and this is meant
              to read as a signal. Fixed-width, shrinking only when a long turn
              is crowded into a narrow column. */}
          {barsFor(t).map((h, i) => (
            <Box
              key={i}
              sx={{
                flex: "0 1 1.5px", maxWidth: "1.5px", minWidth: "1px",
                height: `${h * 100}%`, borderRadius: "1px", bgcolor: color,
              }}
            />
          ))}
        </Stack>
      ))}
    </Box>
  );

  return (
    <Box sx={{ px: 2, pt: 1.5 }}>
      <SectionLabel>Recording</SectionLabel>

      <Box
        sx={{
          mt: 0.75, p: 1.25, borderRadius: 1,
          border: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Box sx={{ position: "relative" }}>
          {/* Gridlines behind both channels, so a turn on one row can be read
              against the clock and against the other speaker. */}
          {ticks.slice(1).map((t) => (
            <Box
              key={t}
              sx={{
                position: "absolute", top: 0, bottom: 0, left: `${(t / total) * 100}%`,
                borderLeft: "1px solid", borderColor: "divider", opacity: 0.6,
              }}
            />
          ))}
          {channel("customer", TALK.customer)}
          {channel("agent", assistant)}
        </Box>

        <Box sx={{ position: "relative", height: 14, mt: 0.5 }}>
          {ticks.map((t, i) => (
            <Typography
              key={t}
              sx={{
                position: "absolute", left: `${(t / total) * 100}%`,
                transform: i === 0 ? "none" : "translateX(-50%)",
                typography: "s3", color: "text.disabled",
                fontFamily: "ui-monospace, Menlo, monospace",
              }}
            >
              {mmss(t)}
            </Typography>
          ))}
        </Box>
      </Box>

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 0.875 }}>
        <Tooltip arrow title="No audio in this prototype — the waveform is drawn from the turns">
          <span>
            <IconButton
              size="small" disabled
              sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
            >
              <Iconify icon="solar:play-bold" width={13} />
            </IconButton>
          </span>
        </Tooltip>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace" }}>
          {mmss(total)}
        </Typography>
        <Box flex={1} />
        <Tooltip arrow title="Downloads the transcript — there is no audio to download">
          <Button
            size="small" variant="outlined" color="inherit"
            onClick={downloadTranscript}
            startIcon={<Iconify icon="solar:download-minimalistic-linear" width={14} />}
            sx={{ typography: "s2", fontWeight: 600, borderColor: "divider", color: "text.secondary" }}
          >
            Download
          </Button>
        </Tooltip>
      </Stack>

      <Stack direction="row" alignItems="center" flexWrap="wrap" rowGap={0.5} spacing={1.5} sx={{ mt: 1.25 }}>
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
          Talk ratio
        </Typography>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: TALK.customer }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Customer {detail.stats.userPct}%</Typography>
        </Stack>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: assistant }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Assistant {detail.stats.aiPct}%</Typography>
        </Stack>
      </Stack>
      {/* Who held the floor, in the order they held it — an assistant that
          talks 60% of a call in one monologue is a different problem from one
          that talks 60% spread across the whole call. */}
      <Stack direction="row" spacing={0.375} sx={{ mt: 0.75, height: 8 }}>
        {turns.map((t) => (
          <Box
            key={t.id}
            sx={{
              flex: Math.max(1, t.dur * 10),
              borderRadius: 0.5,
              bgcolor: t.role === "agent" ? assistant : TALK.customer,
            }}
          />
        ))}
      </Stack>
    </Box>
  );
}

Recording.propTypes = { detail: PropTypes.object };

/* Bar heights for one turn. Seeded by the turn, so a call looks the same every
   time it is opened, and two speakers never draw the same shape. */
const barsFor = (turn) => {
  const h = hashSeed(turn.id);
  const n = Math.max(4, Math.round(turn.dur * 9));
  return Array.from({ length: n }, (_, i) => {
    const v = Math.sin((h % 71) + i * 0.9) * Math.cos(i * 0.37 + (h % 17));
    return 0.22 + Math.abs(v) * 0.78;
  });
};
