import PropTypes from "prop-types";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Chip, Collapse, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { RunTraceLog } from "../../components/RunTrace";
import { outcomeOf } from "../taskOutcome";
import TaskLinks from "./TaskLinks";
import CallScanStrip from "./CallScanStrip";
import OmegaHandoff from "../OmegaHandoff";
import NewAgentVersion from "../NewAgentVersion";
import ImaginePane from "./ImaginePane";

/**
 * What is wrong, and what to change about it.
 *
 * This is the old Fix-my-agent suggestions pane with the diagnosis put back
 * underneath it. The version it replaces opened straight onto a list of
 * recommendations with an insights paragraph above them — which is fine until
 * somebody asks where a recommendation came from, and the honest answer is
 * "a model looked at your run". Six named analyzers can each be argued with,
 * and the one that matters most is about the measurement rather than the agent.
 *
 * The old split into fixable and not-fixable is kept, because it was right —
 * but the line moves. "Not fixable" here is not a shrug at system-level
 * problems; it is the set of changes that belong to the environment rather
 * than the agent, and shipping a prompt edit instead of one of those is how a
 * team spends a month improving a number that was never measuring anything.
 */

/* A change that unblocks a release outranks one that lifts the average. */
const priorityOf = (p, tasks) => {
  const blockers = p.addresses.filter((id) => tasks.find((t) => t.id === id)?.critical).length;
  if (blockers) return { label: "Critical", color: "#DC2626", level: 0 };
  if (p.addresses.length > 2) return { label: "High", color: "#CA8A04", level: 1 };
  return { label: "Medium", color: "#6B7280", level: 2 };
};

/*
  Module-level cache of run ids whose diagnosis animation has already
  played this session. Reopening the drawer for a run in this set
  skips straight to "done" — no repeat animation.
*/
const analyzedRuns = new Set();

/* Reading the whole run takes about nine seconds, however many calls it has;
   a failing call takes longer to read than a clean one, and the jitter keeps
   the strip from ticking like a metronome. */
const SCAN_MS = 9000;
const hashId = (s) => {
  let h = 0;
  for (let i = 0; i < String(s).length; i += 1) h = ((h << 5) - h + String(s).charCodeAt(i)) | 0;
  return Math.abs(h);
};
const readMs = (t, base) => {
  const outcome = outcomeOf(t);
  const weight = outcome === "failed" || outcome === "flaky" ? 1.35 : outcome === "unmeasured" ? 0.6 : 0.85;
  return Math.round(base * weight * (0.7 + (hashId(t?.id) % 60) / 100));
};

/* One call is an anecdote; an issue is shown once its pattern has turned up
   in two of them (or in its only one). */
const CONFIRM_AFTER = 2;

/* The proposal's sentence carries its count ("22 tasks came in under…");
   while the scan is still finding calls, the count is the live one. */
const liveWhy = (why, n) => {
  if (!why) return why;
  return why.replace(
    /^\d+( (?:release |long )?)(task|episode|blocker)s?\b/,
    (_, mid, noun) => `${n}${mid}${noun}${n === 1 ? "" : "s"}`,
  );
};

function TabButton({ active, onClick, icon, children, disabled }) {
  return (
    <Box
      onClick={disabled ? undefined : onClick}
      sx={{
        display: "inline-flex", alignItems: "center", gap: 0.5,
        py: 1.25, cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.45 : 1,
        borderBottom: "2px solid",
        borderColor: active ? "text.primary" : "transparent",
        color: active ? "text.primary" : "text.subtitle",
        "&:hover": { color: "text.primary" },
      }}
    >
      <Iconify icon={icon} width={14} />
      <Typography sx={{ typography: "s2", fontWeight: active ? 700 : 500 }}>{children}</Typography>
    </Box>
  );
}
TabButton.propTypes = {
  active: PropTypes.bool,
  onClick: PropTypes.func,
  icon: PropTypes.string,
  children: PropTypes.node,
  disabled: PropTypes.bool,
};

export default function DiagnosisPane({
  tasks, report, trace, proposals, verdict,
  applied, setApplied, current, projected, willFix,
  measured, failing, onOpenTask, onViewIssue, onOptimize, onClose,
  env, envState, patch, onHandOff, onCreateAgentVersion, onRunNewVersion, runId,
}) {
  /* First open of this runId: play the analyzer animation. Every
     subsequent open in this session: skip straight to the results. */
  const [phase, setPhase] = useState(() => (runId && analyzedRuns.has(runId) ? "done" : "running"));
  useEffect(() => {
    if (phase === "done" && runId) analyzedRuns.add(runId);
  }, [phase, runId]);

  /* The scan: calls are read one at a time, and everything below — the strip,
     the issue cards, their counts — is derived from how far it has got. */
  const total = tasks.length;
  const [scanned, setScanned] = useState(() => (phase === "done" ? total : 0));
  const running = phase === "running";
  const baseMs = SCAN_MS / Math.max(1, total);
  useEffect(() => {
    if (!running) return undefined;
    if (scanned >= total) {
      const t = setTimeout(() => setPhase("done"), 650);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setScanned((n) => n + 1), readMs(tasks[scanned], baseMs));
    return () => clearTimeout(t);
  }, [running, scanned, total, tasks, baseMs]);
  const rescan = () => {
    setScanned(0);
    setPhase("running");
  };
  const [handoff, setHandoff] = useState(false);
  /* The new primary path: fork the agent code, apply the accepted changes,
     mint the next agent version. The old "hand off as PR / patch / ticket"
     stays as a secondary alternative for teams that want the diff in a
     review tool rather than as a bundled version. */
  const [versioning, setVersioning] = useState(false);
  const [open, setOpen] = useState({});
  const [showDiagnosis, setShowDiagnosis] = useState(true);
  /* Diagnosis is the fixed six-analyzer read; Imagine is the freeform
     follow-up canvas over the same run. Kept as a top-level tab inside
     this pane so the user can jump between them without losing the
     drawer's context (env pin, run id, failing task list). */
  const [tab, setTab] = useState("diagnosis");

  const included = proposals.filter((p) => applied[p.id]);
  /* Nothing failed, so there is nothing to search for. The refactor into a
     drawer dropped this branch and left an empty recommendation list under a
     live "Optimize my agent" button — a search over an empty candidate pool
     that would have burned episodes to re-confirm the score it started from. */
  const nothingToFix = !failing.length;

  const sorted = useMemo(() => (
    [...proposals].sort((a, b) =>
      priorityOf(a, tasks).level - priorityOf(b, tasks).level
      || b.addresses.length - a.addresses.length)
  ), [proposals, tasks]);

  /* Where each call sits in the reading order, and the point in the scan at
     which each issue has enough calls behind it to be shown. */
  const found = useMemo(() => {
    const at = new Map(tasks.map((t, i) => [t.id, i]));
    return sorted.map((p, rank) => {
      const idx = p.addresses.map((id) => at.get(id)).filter((i) => i != null).sort((a, b) => a - b);
      const k = Math.min(CONFIRM_AFTER, idx.length);
      return { p, rank, idx, foundAt: k ? idx[k - 1] + 1 : 0 };
    });
  }, [sorted, tasks]);

  /* Discovery order while reading; ranked order once every call is in. Each
     card carries only the calls read so far, so its count, lift and priority
     grow with the scan instead of arriving fully formed. */
  const shownIssues = useMemo(() => {
    if (!running) return sorted.map((p, i) => ({ p, rank: i + 1 }));
    const pct = (n) => (measured.length ? Math.round((n / measured.length) * 100) : 0);
    return found
      .filter((f) => f.idx.length && scanned >= f.foundAt)
      .sort((a, b) => a.foundAt - b.foundAt || a.rank - b.rank)
      .map(({ p, idx }) => {
        const seen = idx.filter((i) => i < scanned).map((i) => tasks[i].id);
        return { p: { ...p, addresses: seen, lift: pct(seen.length), why: liveWhy(p.why, seen.length) }, rank: null };
      });
  }, [running, sorted, found, scanned, tasks, measured.length]);

  /* When the list re-ranks at the end, each card slides from where it was to
     where it belongs rather than jumping. */
  const cardEls = useRef({});
  const lastTops = useRef({});
  const orderKey = shownIssues.map((x) => x.p.id).join("|");
  const lastOrder = useRef(orderKey);
  useLayoutEffect(() => {
    const tops = {};
    Object.entries(cardEls.current).forEach(([id, el]) => { if (el) tops[id] = el.offsetTop; });
    if (lastOrder.current !== orderKey && !running) {
      Object.entries(tops).forEach(([id, top]) => {
        const dy = (lastTops.current[id] ?? top) - top;
        if (dy) {
          cardEls.current[id]?.animate?.(
            [{ transform: `translateY(${dy}px)` }, { transform: "none" }],
            { duration: 520, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" },
          );
        }
      });
    }
    lastOrder.current = orderKey;
    lastTops.current = tops;
  });

  /* Overall run summary — the failures collapse into a handful of causes, and
     fixing the top-ranked one frees a concrete number of scenarios. Kept to
     data this pane already has so it can never disagree with the list below. */
  const causeCount = proposals.length;
  const topFrees = sorted[0]?.addresses.length || 0;
  const alreadyPass = Math.max(0, measured.length - failing.length);

  /* Export the diagnosis as a plain-text report. Defensive: restricted
     sandboxes block programmatic downloads, so a failure is swallowed. */
  const exportReport = () => {
    try {
      const lines = [
        `Run report${runId ? ` — ${runId}` : ""}`,
        `${failing.length} failing of ${measured.length} measured`,
        "",
        `Summary: ${verdict}`,
        "",
        "Fix in this order:",
        ...sorted.map((p, i) => {
          const pr = priorityOf(p, tasks);
          return `  ${i + 1}. [${pr.label}] ${p.title} — +${p.lift}%, ${p.addresses.length} call${p.addresses.length === 1 ? "" : "s"}${p.why ? `\n     ${p.why}` : ""}`;
        }),
      ];
      const blob = new Blob([lines.join("\n")], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${runId || "run"}-report.txt`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      /* download blocked (sandbox / private mode) — no-op */
    }
  };

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      {/* ── header ── */}
      <Stack
        direction="row" alignItems="center" spacing={1.25}
        sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Box
          sx={{
            width: 30, height: 30, borderRadius: 1, display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1), color: "#7857FC",
          }}
        >
          <Iconify icon="solar:magic-stick-3-linear" width={16} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Debug failures</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {failing.length} failing of {measured.length} measured
            {tasks.length - measured.length ? ` · ${tasks.length - measured.length} not measured` : ""}
          </Typography>
        </Box>
        {phase === "done" && !nothingToFix && (
          <Button
            size="small"
            onClick={exportReport}
            startIcon={<Iconify icon="solar:upload-minimalistic-linear" width={14} />}
            sx={{
              typography: "s3", fontWeight: 700, color: "text.secondary",
              textTransform: "none", height: 28, px: 1,
              "&:hover": { bgcolor: "transparent", color: "text.primary" },
            }}
          >
            Export report
          </Button>
        )}
        {phase === "done" && (
          <Tooltip arrow title="Read the run again">
            <IconButton size="small" onClick={rescan}>
              <Iconify icon="solar:refresh-linear" width={16} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
        )}
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="eva:close-fill" width={17} />
        </IconButton>
      </Stack>

      {/* ── tab bar — there from the start so nothing jumps when the scan
            ends; Imagine waits for the diagnosis it reads from. ── */}
      {!(phase === "done" && nothingToFix) && (
        <Stack
          direction="row" spacing={2}
          sx={{ px: 2.5, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <TabButton active={tab === "diagnosis"} onClick={() => setTab("diagnosis")} icon="solar:document-medicine-linear">
            Diagnosis
          </TabButton>
          <TabButton
            active={tab === "imagine"} onClick={() => setTab("imagine")} icon="solar:magic-stick-3-linear"
            disabled={running || nothingToFix}
          >
            Imagine
          </TabButton>
        </Stack>
      )}

      {phase === "done" && nothingToFix && (
        <Stack alignItems="center" justifyContent="center" spacing={1.5} sx={{ flex: 1, px: 4, textAlign: "center" }}>
          <Box
            sx={{
              width: 44, height: 44, borderRadius: 1.5, display: "grid", placeItems: "center",
              bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1), color: "#16A34A",
            }}
          >
            <Iconify icon="solar:cup-star-linear" width={22} />
          </Box>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Nothing to fix</Typography>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            Every measured scenario passed and no analyzer found a problem with the measurement. Optimizing
            against this run would spend episodes confirming the score it started from — add harder scenarios
            to find the edges instead.
          </Typography>
        </Stack>
      )}

      {phase === "done" && !nothingToFix && tab === "imagine" && (
        <ImaginePane tasks={tasks} env={env} />
      )}

      {(running || !nothingToFix) && tab === "diagnosis" && (
        <>
          <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
            {/* ── how far the read has got ── */}
            <Collapse in={running} unmountOnExit>
              <Box sx={{ px: 2.5, pt: 2.5 }}>
                <CallScanStrip tasks={tasks} scanned={scanned} />
              </Box>
            </Collapse>

            {/* ── diagnosis — the analyzers read across the whole run, so
                 they wait for the last call and then report together ── */}
            <Box sx={{ px: 2.5, pt: 2.5 }}>
              <Stack
                direction="row" alignItems="center" spacing={0.75}
                onClick={running ? undefined : () => setShowDiagnosis((v) => !v)}
                sx={{ cursor: running ? "default" : "pointer", mb: 1 }}
              >
                <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>Diagnosis</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {running
                    ? `${report.length} analyzers · report once every call is read`
                    : `${report.length} analyzers`}
                </Typography>
                {!running && (
                  <Iconify
                    icon={showDiagnosis ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
                    width={15} sx={{ color: "text.subtitle" }}
                  />
                )}
              </Stack>
              <Collapse in={!running && showDiagnosis}>
                <Stack
                  divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
                  sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
                >
                  {report.map((a, i) => {
                    return (
                      <Box
                        key={a.id}
                        sx={{
                          animation: `diag-in 320ms ease ${i * 90}ms both`,
                          "@keyframes diag-in": { from: { opacity: 0, transform: "translateY(-4px)" } },
                        }}
                      >
                        <Stack
                          direction="row" alignItems="center" spacing={1}
                          onClick={() => setOpen((o) => ({ ...o, [a.id]: !o[a.id] }))}
                          sx={{ px: 1.25, py: 0.625, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
                        >
                          <Typography sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}>{a.label}</Typography>
                          {a.always && (
                            <Typography sx={{ typography: "s3", color: "text.disabled", flexShrink: 0 }}>· always on</Typography>
                          )}
                          <Typography
                            noWrap
                            sx={{
                              typography: "s3", fontWeight: 500, flex: 1, minWidth: 0,
                              color: "text.secondary",
                            }}
                          >
                            {a.headline}
                          </Typography>
                          <Iconify
                            icon={open[a.id] ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
                            width={12} sx={{ color: "text.subtitle", flexShrink: 0 }}
                          />
                        </Stack>
                        <Collapse in={!!open[a.id]}>
                          <Box sx={{ px: 1.25, pb: 1.75 }}>
                            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}>
                              reads {a.reads}
                            </Typography>
                            <Typography sx={{ typography: "s2", color: "text.secondary", mb: a.rows?.length ? 1.25 : 0 }}>
                              {a.detail}
                            </Typography>
                            <Stack spacing={0.75}>
                              {(a.rows || []).map((r) => (
                                <Box key={r.label}>
                                  <Typography sx={{ typography: "s2" }}>
                                    {r.label}
                                    {r.value != null && (
                                      <Box component="span" sx={{ color: "text.subtitle", ml: 0.75 }}>{r.value}</Box>
                                    )}
                                  </Typography>
                                  {r.note && (
                                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{r.note}</Typography>
                                  )}
                                  <TaskLinks ids={r.tasks} tasks={tasks} step={r.step} onOpen={onOpenTask} />
                                </Box>
                              ))}
                            </Stack>
                          </Box>
                        </Collapse>
                      </Box>
                    );
                  })}
                </Stack>
                <Box sx={{ mt: 0.5, mx: -1 }}>
                  <RunTraceLog label={`Read ${tasks.length} episodes`} steps={trace} />
                </Box>
              </Collapse>
            </Box>

            {/* ── overall run summary — sits after the diagnosis so it reads as
                 the takeaway from the analyzers above, not a claim ahead of them ── */}
            <Collapse in={!running}>
              <Box
                sx={{
                  mx: 2.5, mt: 2.5, px: 1.75, py: 1.5, borderRadius: 1,
                  border: "1px solid",
                  borderColor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.35 : 0.25),
                  bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.1 : 0.05),
                }}
              >
                <Stack direction="row" alignItems="flex-start" spacing={1.25}>
                  <Iconify icon="solar:lightbulb-bolt-linear" width={15} sx={{ color: "#7857FC", flexShrink: 0, mt: "2px" }} />
                  <Typography sx={{ typography: "s2", fontWeight: 700 }}>{verdict}</Typography>
                </Stack>
                {causeCount > 0 && topFrees > 0 && (
                  <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1, pl: 3.375 }}>
                    These <b>{failing.length} failures</b> collapse into{" "}
                    <b>{causeCount} {causeCount === 1 ? "cause" : "causes"}</b>. Fix the top one and{" "}
                    <b>{topFrees} {topFrees === 1 ? "scenario flips" : "scenarios flip"}</b> green.
                  </Typography>
                )}
                {alreadyPass > 0 && (
                  <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.75, pl: 3.375 }}>
                    {alreadyPass} of {measured.length} measured scenarios already pass — the gaps are
                    concentrated, not scattered.
                  </Typography>
                )}
              </Box>
            </Collapse>

            {/*
              Create a new agent version from the diagnosis.

              The primary product concept: because the environment was built
              from the agent's own source, we hold the code. When a diagnosis
              produces changes worth making, we can fork the current agent —
              apply the accepted diffs — and mint it as the next agent
              version, right here. The user then runs the new version against
              the same environment and the compare feature reads v1 against
              v2 on the same scenarios.
            */}
            {!running && versioning && !!included.length && (
              <Box sx={{ px: 2.5, pt: 2.5 }}>
                <NewAgentVersion
                  env={env}
                  envState={envState}
                  included={included}
                  projected={projected}
                  current={current}
                  willFix={willFix}
                  onCreate={({ note, applied: appliedChanges }) =>
                    onCreateAgentVersion?.(appliedChanges, projected, note)}
                  onRun={(version) => onRunNewVersion?.(version)}
                />
              </Box>
            )}

            {/*
              Hand off without minting a version.

              Kept as the quieter alternative — a tool description one
              scenario obviously needs is sometimes a review-in-Git change,
              not a bundled version. This is the same OmegaHandoff panel as
              before, just no longer the primary path.
            */}
            {!running && handoff && !!included.length && (
              <Box sx={{ px: 2.5, pt: 2.5 }}>
                <OmegaHandoff
                  env={env}
                  envState={envState}
                  patch={patch}
                  included={included}
                  projected={projected}
                  current={current}
                  willFix={willFix}
                  onRerun={() => onHandOff?.(included, projected)}
                />
              </Box>
            )}

            {/* ── fixable: change the agent, in priority order ── */}
            <Box sx={{ px: 2.5, pt: 2.5, pb: 2.5 }}>
              <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1 }}>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                  {running ? "Issues found" : "Fix in this order"}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {running
                    ? `${shownIssues.length} so far · ranked once every call is read`
                    : "ranked by severity, then how many scenarios each one frees"}
                </Typography>
              </Stack>
              {running && !shownIssues.length && (
                <Box
                  sx={{
                    px: 2, py: 2.5, borderRadius: 1, border: "1px dashed", borderColor: "divider",
                    textAlign: "center",
                  }}
                >
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    Each issue appears here as soon as two calls confirm it, with the calls it affects and the change
                    that addresses it.
                  </Typography>
                </Box>
              )}
              <Stack spacing={1} sx={{ position: "relative" }}>
                {shownIssues.map(({ p, rank }) => {
                  const pr = priorityOf(p, tasks);
                  return (
                    <Box
                      key={p.id}
                      ref={(el) => { cardEls.current[p.id] = el; }}
                      sx={{
                        /* Presentation-only card — every proposal is
                           included by default; the checkbox was removed
                           because the Self Improvement run acts on the
                           whole diagnosis, not a user-picked subset. */
                        p: 1.75, borderRadius: 1, border: "1px solid",
                        borderColor: "divider",
                        bgcolor: "transparent",
                        /* A card arrives with a brief accent edge so the eye
                           finds the new one, then settles like the rest. */
                        ...(running && {
                          animation: "issue-in 1600ms ease both",
                          "@keyframes issue-in": {
                            "0%": { opacity: 0, transform: "translateY(-6px)", borderColor: "#7857FC" },
                            "18%": { opacity: 1, transform: "none", borderColor: "#7857FC" },
                          },
                        }),
                      }}
                    >
                      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
                        {/* Rank — the order to work through them. Until every
                            call is read there is no order yet; the slot is
                            held so the card does not shift when it lands. */}
                        {rank != null ? (
                          <Typography sx={{
                            typography: "s1", fontWeight: 800, color: "text.disabled",
                            width: 18, textAlign: "center", flexShrink: 0, lineHeight: 1.35,
                            fontVariantNumeric: "tabular-nums",
                          }}>
                            {rank}
                          </Typography>
                        ) : (
                          <Box sx={{ width: 18, flexShrink: 0 }} />
                        )}
                        <Box flex={1} minWidth={0}>
                          <Stack direction="row" alignItems="center" spacing={0.625} flexWrap="wrap" rowGap={0.375} sx={{ mb: 0.375 }}>
                            <Chip
                              size="small" label={p.kind}
                              sx={{
                                height: 18, borderRadius: 0.5, color: "text.secondary",
                                border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                                "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 600 },
                              }}
                            />
                            {pr && (
                              <Chip
                                size="small" label={pr.label}
                                sx={{
                                  height: 18, borderRadius: 0.5, color: pr.color,
                                  border: "1px solid", borderColor: alpha(pr.color, 0.4), bgcolor: "transparent",
                                  "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 700 },
                                }}
                              />
                            )}
                            <Typography sx={{ typography: "s3", color: "#16A34A", fontWeight: 700 }}>
                              +{p.lift}%
                            </Typography>
                          </Stack>
                          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{p.title}</Typography>
                          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>{p.why}</Typography>
                          {/*
                            Impact + action row. Replaces the wall of
                            per-task chips (TaskLinks) that used to
                            sit here — a "N calls affected" number
                            plus a "View affected calls" button reads
                            faster and pushes the drawer toward
                            action rather than reading. Clicking
                            "View" filters the trace table to the
                            exact task ids this finding addresses.
                          */}
                          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mt: 1 }}>
                            <Stack direction="row" alignItems="center" spacing={0.5}>
                              <Iconify icon="solar:play-circle-linear" width={13} sx={{ color: "text.subtitle" }} />
                              <Typography sx={{
                                typography: "s3", fontWeight: 600, color: "text.secondary",
                                fontVariantNumeric: "tabular-nums",
                              }}>
                                {p.addresses.length} call{p.addresses.length === 1 ? "" : "s"} affected
                              </Typography>
                            </Stack>
                            {onViewIssue && p.addresses.length > 0 && (
                              <Button
                                size="small" variant="outlined"
                                onClick={() => onViewIssue(p.title, p.addresses)}
                                startIcon={<Iconify icon="solar:eye-linear" width={14} />}
                                sx={{
                                  typography: "s3", fontWeight: 700,
                                  color: "text.primary", borderColor: "divider",
                                  height: 26, px: 1,
                                  "&:hover": { borderColor: "text.primary", bgcolor: "transparent" },
                                }}
                              >
                                View affected calls
                              </Button>
                            )}
                          </Stack>
                          <Stack spacing={0.375} sx={{ mt: 1.125 }}>
                            {p.diff.map((d) => (
                              <Stack
                                key={d.text} direction="row" spacing={0.875}
                                sx={{
                                  px: 1.125, py: 0.75, borderRadius: 0.75,
                                  bgcolor: (t) => alpha(d.type === "add" ? "#16A34A" : "#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
                                }}
                              >
                                <Typography sx={{ typography: "s3", fontWeight: 700, color: d.type === "add" ? "#16A34A" : "#DC2626", flexShrink: 0 }}>
                                  {d.type === "add" ? "+" : "−"}
                                </Typography>
                                <Typography
                                  sx={{
                                    typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                                    color: d.type === "add" ? "text.primary" : "text.disabled",
                                    textDecoration: d.type === "add" ? "none" : "line-through",
                                  }}
                                >
                                  {d.text}
                                </Typography>
                              </Stack>
                            ))}
                          </Stack>
                        </Box>
                      </Stack>
                    </Box>
                  );
                })}
              </Stack>
            </Box>
          </Box>

          {/* ── the footer that never scrolls away ── */}
          <Stack
            spacing={1.25}
            sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
          >
            {/*
              The concept correction: the primary path is the optimizer.
              The diagnosis names the candidate changes; the optimizer
              searches over the checked ones — trying combinations,
              scoring each on the training scenarios — and the code
              actually evolves trial by trial in the pane below. The
              winner is the version worth running.

              "Hand off" stays as the quiet alternative for teams that
              would rather review the diff in Git than watch a search.
              The direct "Create agent version" shortcut was removed:
              the correct way to mint a version from a diagnosis is to
              let the optimizer pick the best combination, not to bundle
              whatever was ticked.
            */}
            <Tooltip
              arrow
              title={
                running
                  ? "Available once every call is read — the self improver searches over the full diagnosis."
                  : included.length
                    ? ""
                    : "Tick at least one change in the list above — the self improver needs a candidate pool to search over."
              }
              placement="top"
            >
              <Box>
                <Button
                  fullWidth variant="contained" color="primary"
                  disabled={running || !included.length}
                  onClick={onOptimize}
                  startIcon={<Iconify icon="solar:magic-stick-3-bold" width={16} />}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  Run Self Improvement
                </Button>
              </Box>
            </Tooltip>
            {/*
              Fallback link, not a co-equal button. Optimize is the
              default; hand-off is for teams that would rather see the
              diff in a review tool before it becomes a version. The
              earlier full-width outlined button read as an equal
              alternative, which it isn't.
            */}
            <Box sx={{ textAlign: "center", pt: 0.25 }}>
              <Button
                size="small"
                disabled={running || !included.length}
                onClick={() => { setHandoff((v) => !v); setVersioning(false); }}
                sx={{
                  typography: "s3", fontWeight: 600,
                  color: "text.subtitle",
                  textTransform: "none",
                  "&:hover": { bgcolor: "transparent", color: "text.primary" },
                }}
              >
                {handoff ? "Hide hand-off" : "or hand off as a PR / patch / ticket"}
              </Button>
            </Box>
          </Stack>
        </>
      )}
    </Stack>
  );
}

DiagnosisPane.propTypes = {
  tasks: PropTypes.array,
  report: PropTypes.array,
  trace: PropTypes.array,
  proposals: PropTypes.array,
  verdict: PropTypes.string,
  applied: PropTypes.object,
  setApplied: PropTypes.func,
  current: PropTypes.number,
  projected: PropTypes.number,
  willFix: PropTypes.number,
  measured: PropTypes.array,
  failing: PropTypes.array,
  onOpenTask: PropTypes.func,
  onViewIssue: PropTypes.func,
  runId: PropTypes.string,
  onOptimize: PropTypes.func,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  onHandOff: PropTypes.func,
  onCreateAgentVersion: PropTypes.func,
  onRunNewVersion: PropTypes.func,
};
