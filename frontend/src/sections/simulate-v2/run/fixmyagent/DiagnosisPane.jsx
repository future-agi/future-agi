import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Chip, Collapse, IconButton, Tooltip, Checkbox } from "@mui/material";
import Iconify from "src/components/iconify";
import { RunTracePanel, RunTraceLog } from "../../components/RunTrace";
import TaskLinks from "./TaskLinks";
import OmegaHandoff from "../OmegaHandoff";
import NewAgentVersion from "../NewAgentVersion";

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

const SEV_TONE = {
  high: { color: "#DC2626", icon: "solar:danger-triangle-bold" },
  medium: { color: "#CA8A04", icon: "solar:info-circle-bold" },
  low: { color: "text.disabled", icon: "solar:minus-circle-linear" },
  clear: { color: "#16A34A", icon: "solar:check-circle-bold" },
};

/* A change that unblocks a release outranks one that lifts the average. */
const priorityOf = (p, tasks) => {
  const blockers = p.addresses.filter((id) => tasks.find((t) => t.id === id)?.critical).length;
  if (blockers) return { label: "Release blocker", color: "#DC2626" };
  if (p.addresses.length > 1) return { label: "High", color: "#CA8A04" };
  return null;
};

export default function DiagnosisPane({
  tasks, report, trace, proposals, checks, verdict,
  applied, setApplied, current, projected, willFix,
  measured, failing, onOpenTask, onOptimize, onApplyChecks, checksApplied, onClose,
  env, envState, patch, onHandOff, onCreateAgentVersion, onRunNewVersion,
}) {
  const [phase, setPhase] = useState("running");
  const [handoff, setHandoff] = useState(false);
  /* The new primary path: fork the agent code, apply the accepted changes,
     mint the next agent version. The old "hand off as PR / patch / ticket"
     stays as a secondary alternative for teams that want the diff in a
     review tool rather than as a bundled version. */
  const [versioning, setVersioning] = useState(false);
  const [open, setOpen] = useState({});
  const [showDiagnosis, setShowDiagnosis] = useState(true);

  const included = proposals.filter((p) => applied[p.id]);
  /* Nothing failed, so there is nothing to search for. The refactor into a
     drawer dropped this branch and left an empty recommendation list under a
     live "Optimize my agent" button — a search over an empty candidate pool
     that would have burned episodes to re-confirm the score it started from. */
  const nothingToFix = !failing.length && !checks.length;

  const sorted = useMemo(() => {
    const rank = (p) => {
      const pr = priorityOf(p, tasks);
      return pr?.label === "Release blocker" ? 0 : pr ? 1 : 2;
    };
    return [...proposals].sort((a, b) => rank(a) - rank(b) || b.addresses.length - a.addresses.length);
  }, [proposals, tasks]);

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
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Fix my agent</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {failing.length} failing of {measured.length} measured
            {tasks.length - measured.length ? ` · ${tasks.length - measured.length} not measured` : ""}
          </Typography>
        </Box>
        {phase === "done" && (
          <Tooltip arrow title="Read the run again">
            <IconButton size="small" onClick={() => setPhase("running")}>
              <Iconify icon="solar:refresh-linear" width={16} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
        )}
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="eva:close-fill" width={17} />
        </IconButton>
      </Stack>

      {phase === "running" && (
        <Box sx={{ flex: 1, overflowY: "auto" }}>
          <RunTracePanel
            title="Reading this run"
            subtitle={`Six analyzers over ${tasks.length} episodes`}
            steps={trace}
            stepMs={700}
            onDone={() => setPhase("done")}
          />
        </Box>
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

      {phase === "done" && !nothingToFix && (
        <>
          <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
            {/* ── the one sentence ── */}
            <Stack
              direction="row" alignItems="flex-start" spacing={1.25}
              sx={{
                mx: 2.5, mt: 2, px: 1.75, py: 1.5, borderRadius: 1,
                bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.1 : 0.05),
              }}
            >
              <Iconify icon="solar:lightbulb-bolt-linear" width={15} sx={{ color: "#7857FC", flexShrink: 0, mt: "2px" }} />
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>{verdict}</Typography>
            </Stack>

            {/* ── diagnosis ── */}
            <Box sx={{ px: 2.5, pt: 2.5 }}>
              <Stack
                direction="row" alignItems="center" spacing={0.75}
                onClick={() => setShowDiagnosis((v) => !v)}
                sx={{ cursor: "pointer", mb: 1 }}
              >
                <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>Diagnosis</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {report.length} analyzers
                </Typography>
                <Iconify
                  icon={showDiagnosis ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
                  width={15} sx={{ color: "text.subtitle" }}
                />
              </Stack>
              <Collapse in={showDiagnosis}>
                <Stack
                  divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
                  sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
                >
                  {report.map((a) => {
                    const tone = a.clear ? SEV_TONE.clear : SEV_TONE[a.severity];
                    return (
                      <Box key={a.id}>
                        <Stack
                          direction="row" alignItems="flex-start" spacing={1.25}
                          onClick={() => setOpen((o) => ({ ...o, [a.id]: !o[a.id] }))}
                          sx={{ px: 1.75, py: 1.375, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
                        >
                          <Iconify icon={tone.icon} width={15} sx={{ color: tone.color, flexShrink: 0, mt: "1px" }} />
                          <Box flex={1} minWidth={0}>
                            <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" rowGap={0.25}>
                              <Typography sx={{ typography: "s2", fontWeight: 700 }}>{a.label}</Typography>
                              {a.always && (
                                <Chip
                                  size="small" label="always on"
                                  sx={{
                                    height: 16, borderRadius: 0.5, color: "text.subtitle",
                                    border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                                    "& .MuiChip-label": { px: 0.5, typography: "s3", fontWeight: 600 },
                                  }}
                                />
                              )}
                            </Stack>
                            <Typography
                              sx={{
                                typography: "s2", fontWeight: 600, mt: 0.125,
                                color: tone.color === "text.disabled" ? "text.secondary" : tone.color,
                              }}
                            >
                              {a.headline}
                            </Typography>
                          </Box>
                          <Iconify
                            icon={open[a.id] ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
                            width={14} sx={{ color: "text.subtitle", flexShrink: 0 }}
                          />
                        </Stack>
                        <Collapse in={!!open[a.id]}>
                          <Box sx={{ px: 1.75, pb: 1.75, pl: 4.75 }}>
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

            {/* ── not fixable by a prompt: the measurement ── */}
            {!!checks.length && (
              <Box sx={{ px: 2.5, pt: 2.5 }}>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>Fix the measurement first</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
                  These change the environment, not the agent — and no prompt edit substitutes for them.
                </Typography>
                <Stack
                  spacing={0}
                  divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
                  sx={{ border: "1px solid", borderColor: alpha("#DC2626", 0.3), borderRadius: 1 }}
                >
                  {checks.map((c) => (
                    <Box key={c.id} sx={{ p: 1.75 }}>
                      <Chip
                        size="small" label={c.kind}
                        sx={{
                          height: 18, borderRadius: 0.5, color: "text.secondary", mb: 0.5,
                          border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                          "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 600 },
                        }}
                      />
                      <Typography sx={{ typography: "s2", fontWeight: 700 }}>{c.title}</Typography>
                      <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>{c.why}</Typography>
                      <TaskLinks ids={c.addresses} tasks={tasks} step={c.step} onOpen={onOpenTask} />
                    </Box>
                  ))}
                  <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.75, py: 1.375 }}>
                    <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
                      {checksApplied
                        ? `Applied as environment ${checksApplied}.`
                        : "Expect the pass rate to fall — these stop counting passes that were never earned."}
                    </Typography>
                    <Button
                      size="small" variant="outlined" disabled={!!checksApplied} onClick={onApplyChecks}
                      sx={{ typography: "s2", fontWeight: 700, flexShrink: 0, color: "text.primary", borderColor: "divider" }}
                    >
                      {checksApplied ? "Updated" : "Apply to checks"}
                    </Button>
                  </Stack>
                </Stack>
              </Box>
            )}

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
            {versioning && !!included.length && (
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
            {handoff && !!included.length && (
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

            {/* ── fixable: change the agent ── */}
            <Box sx={{ px: 2.5, pt: 2.5, pb: 2.5 }}>
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>Change the agent</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
                Include the ones to act on — they seed the optimizer and travel into the hand-off.
              </Typography>
              <Stack spacing={1}>
                {sorted.map((p) => {
                  const on = !!applied[p.id];
                  const pr = priorityOf(p, tasks);
                  return (
                    <Box
                      key={p.id}
                      onClick={() => setApplied((a) => ({ ...a, [p.id]: !a[p.id] }))}
                      sx={{
                        /*
                          Neutral card in every state — the earlier
                          purple fill + border on selected rows read as
                          a wash of colour once every proposal was
                          checked by default. The checkbox alone now
                          carries the "included" signal.
                        */
                        p: 1.75, borderRadius: 1, cursor: "pointer", border: "1px solid",
                        borderColor: "divider",
                        bgcolor: "transparent",
                        "&:hover": { borderColor: "text.disabled" },
                      }}
                    >
                      <Stack direction="row" alignItems="flex-start" spacing={1.25}>
                        {/*
                          Real checkbox instead of an Iconify circle — the
                          filled-circle icon didn't read as "click me to
                          include this change". Every proposal is checked
                          on open (see FixMyAgentDrawer's seed effect) so
                          the primary CTA is armed by default; users
                          uncheck what they don't want to bundle.
                        */}
                        <Checkbox
                          size="small" checked={on}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => setApplied((a) => ({ ...a, [p.id]: e.target.checked }))}
                          sx={{
                            p: 0, mt: "1px", flexShrink: 0,
                            color: "text.disabled",
                            "&.Mui-checked": { color: "#7857FC" },
                          }}
                        />
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
                          <TaskLinks ids={p.addresses} tasks={tasks} step={p.step} onOpen={onOpenTask} />
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
            <Stack direction="row" alignItems="center" spacing={2}>
              <Box>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Now</Typography>
                <Typography sx={{ typography: "s1", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                  {current}%
                </Typography>
              </Box>
              <Iconify icon="solar:arrow-right-linear" width={16} sx={{ color: "text.subtitle" }} />
              <Box>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  With {included.length} included
                </Typography>
                <Typography
                  sx={{ typography: "s1", fontWeight: 700, color: "#16A34A", fontVariantNumeric: "tabular-nums" }}
                >
                  {projected}%
                </Typography>
              </Box>
              <Box flex={1} />
              {willFix > 0 && (
                <Typography sx={{ typography: "s3", color: "text.subtitle", textAlign: "right", maxWidth: 150 }}>
                  if all {willFix} addressed {willFix === 1 ? "task" : "tasks"} pass
                </Typography>
              )}
            </Stack>
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
                included.length
                  ? ""
                  : "Tick at least one change in the list above — the optimizer needs a candidate pool to search over."
              }
              placement="top"
            >
              <Box>
                <Button
                  fullWidth variant="contained" color="primary"
                  disabled={!included.length}
                  onClick={onOptimize}
                  startIcon={<Iconify icon="solar:magic-stick-3-bold" width={16} />}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  {included.length
                    ? `Optimize my agent with ${included.length} ${included.length === 1 ? "change" : "changes"}`
                    : "Select changes to optimize my agent"}
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
                disabled={!included.length}
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
  checks: PropTypes.array,
  verdict: PropTypes.string,
  applied: PropTypes.object,
  setApplied: PropTypes.func,
  current: PropTypes.number,
  projected: PropTypes.number,
  willFix: PropTypes.number,
  measured: PropTypes.array,
  failing: PropTypes.array,
  onOpenTask: PropTypes.func,
  onOptimize: PropTypes.func,
  onApplyChecks: PropTypes.func,
  checksApplied: PropTypes.string,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  onHandOff: PropTypes.func,
  onCreateAgentVersion: PropTypes.func,
  onRunNewVersion: PropTypes.func,
};
