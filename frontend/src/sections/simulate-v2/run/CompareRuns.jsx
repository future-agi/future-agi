import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { alpha, useTheme } from "@mui/material/styles";
import ReactApexChart from "react-apexcharts";
import { useSnackbar } from "notistack";
import {
  Box, Stack, Typography, Button, IconButton, TextField, MenuItem, Tooltip, Menu, Checkbox,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { getEnvironment } from "../_mock/environments";
import { protoRunId } from "../_mock/executionAdapter";
import {
  buildComparison, distributionFor, changedCount, runSummaries,
} from "../_mock/comparison";
import {
  emptyFilters, filterRows, sortRows, applyQuick, groupRows, defaultView,
  behaviourDiff, ROW_HEIGHTS, viewSnapshot, snapshotsEqual, newViewId,
} from "../_mock/compareView";
import { useEnvState, useSimStore } from "../store";
import { SectionCard, EmptyState, PersonaBadge, Verdict, DomainChip } from "../components/primitives";
import CallCompare from "./CallCompare";
import CompareActions, { CompareSearchBar, SavedViews } from "./CompareControls";
import CompareGrid from "./CompareGrid";
import CompareSummary from "./CompareSummary";

/**
 * Two or more runs of the same scenarios.
 *
 * This is the run detail screen with a second question: not "what happened"
 * but "what changed". Every row is one scenario, and every run gets its own
 * line in it — its verdict, and the single thing that decided it.
 *
 * A whole transcript per run per row would be the honest rendering and an
 * unreadable one, so the row carries the deciding moment and the transcripts
 * are one click away. Turning on Show diff swaps that line for what this run
 * did *differently* — which tools it stopped calling, where it first diverged —
 * because for two versions of one agent the change in behaviour is the finding,
 * and it is the thing a transcript hides.
 *
 * The first run in the URL is the baseline. Everything else is read against it.
 */
export default function CompareRuns() {
  const { envId } = useParams();
  const navigate = useNavigate();
  const theme = useTheme();
  const { enqueueSnackbar } = useSnackbar();
  const [params, setParams] = useSearchParams();
  const { state } = useSimStore();
  const { envState, patch } = useEnvState(envId);

  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState(emptyFilters);
  const [selected, setSelected] = useState([]);
  const [openRow, setOpenRow] = useState(null);
  const [addAnchor, setAddAnchor] = useState(null);
  const [pickedEval, setPickedEval] = useState(null);
  /*
    The view is derived from the saved default unless this session has changed
    it. Seeding state from `envState` instead would read the store before it
    hydrates and lock the screen to the built-in default.
  */
  const [viewOverride, setViewOverride] = useState(null);
  const view = viewOverride || { ...defaultView(), ...(envState.compareView || {}) };

  /*
    Saved views live on the environment and the active one lives in the URL, so
    a link carries the reading as well as the runs — the same contract Observe
    has with `?tab=view-…`.
  */
  const savedViews = envState.compareViews || [];
  const activeViewId = params.get("view") || null;
  const activeView = savedViews.find((v) => v.id === activeViewId) || null;
  const snapshot = viewSnapshot(filters, view);
  const dirty = activeView
    ? !snapshotsEqual(snapshot, activeView.config)
    : !snapshotsEqual(snapshot, viewSnapshot(emptyFilters(), { ...defaultView(), ...(envState.compareView || {}) }));

  const setActiveView = (id) => {
    const next = new URLSearchParams(params);
    if (id) next.set("view", id); else next.delete("view");
    setParams(next, { replace: true });
  };

  const applyView = (id) => {
    const v = savedViews.find((x) => x.id === id);
    setFilters(v ? { ...v.config.filters } : emptyFilters());
    setViewOverride(v ? { ...v.config.view } : { ...defaultView(), ...(envState.compareView || {}) });
    setActiveView(id);
  };

  const saveView = (name) => {
    const id = newViewId(name, savedViews);
    patch({ compareViews: [...savedViews, { id, name, config: snapshot }] });
    setActiveView(id);
    enqueueSnackbar(`View “${name}” saved`, { variant: "success" });
  };

  const updateView = (id) => {
    patch({ compareViews: savedViews.map((v) => (v.id === id ? { ...v, config: snapshot } : v)) });
    enqueueSnackbar("View updated", { variant: "success" });
  };

  const renameView = (id, name) =>
    patch({ compareViews: savedViews.map((v) => (v.id === id ? { ...v, name } : v)) });

  const deleteView = (id) => {
    patch({ compareViews: savedViews.filter((v) => v.id !== id) });
    if (id === activeViewId) setActiveView(null);
  };

  const env = getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);
  const runIds = useMemo(() => (params.get("runs") || "").split(",").filter(Boolean), [params]);
  const comparison = useMemo(
    () => (env ? buildComparison(env, envState, runIds) : { runs: [], rows: [], evals: [] }),
    [env, envState, runIds],
  );
  const others = useMemo(
    () => (env ? runSummaries(env, envState).filter((r) => !runIds.includes(r.id)) : []),
    [env, envState, runIds],
  );

  const evalId = comparison.evals.some((e) => e.id === pickedEval)
    ? pickedEval
    : comparison.evals[0]?.id || null;

  /* Search, then the panel's fields, then the quick filters, then order. */
  const rows = useMemo(() => {
    const found = filterRows(comparison.rows, { query, filters, evals: comparison.evals });
    return sortRows(applyQuick(found, view.quick), view.sort);
  }, [comparison.rows, comparison.evals, query, filters, view.quick, view.sort]);
  const groups = useMemo(() => groupRows(rows, view.group), [rows, view.group]);

  const back = () => navigate(paths.dashboard.simulate.environmentStep(envId, "runs"));
  const setRuns = (ids) => setParams({ runs: ids.join(",") }, { replace: true });

  const exportComparison = () => {
    const payload = {
      environment: env?.name,
      baseline: comparison.baseline?.id,
      runs: comparison.runs.map((r) => ({
        id: r.id, agentVersion: r.agentVersion, passRate: r.passRate, tokens: r.tokens, cost: r.cost,
      })),
      scenarios: rows.map((row) => ({
        scenario: row.title,
        critical: !!row.critical,
        movement: row.broke ? "broke" : row.fixed ? "fixed" : row.changed ? "changed" : "unchanged",
        runs: row.cells.map((c) => ({
          run: c.runId, verdict: c.status, deciding: c.deciding.text,
          durationMs: c.durationMs, tokens: c.tokens,
        })),
      })),
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${env?.id || "comparison"}-compare.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!env || comparison.runs.length < 2) {
    return (
      <Box sx={{ p: 3 }}>
        <EmptyState
          icon="solar:transfer-horizontal-linear"
          title="Nothing to compare"
          body="Pick two or more runs from the summary and press Compare."
          action={<Button variant="contained" color="primary" size="small" onClick={back}>Back to runs</Button>}
        />
      </Box>
    );
  }

  const changed = changedCount(comparison);

  /* n is part of the measurement. Runs sampled a different number of times are
     not reporting the same quantity, and the header says so rather than
     letting two differently-sampled rates sit side by side as equals. */
  const sampleNote = comparison.coverage.repeats.length === 1
    ? ` · ${comparison.coverage.repeats[0]} samples each`
    : ` · samples per scenario differ (${comparison.coverage.repeats.join(", ")})`;

  /*
    Re-running is the loop closing. It starts a real run of exactly the rows
    someone ticked, against whatever the agent is now — which is the reason
    they were reading a comparison in the first place.
  */
  /*
    Optimize reads failures against the run they happened in, so the handover
    names a run: the latest one compared, with the ticked scenarios in tow.
  */
  const optimizeSelected = () => {
    const target = comparison.runs[comparison.runs.length - 1];
    navigate(
      `${paths.dashboard.simulate.simulationRun(env.id, target.id)}?tab=omega&only=${selected.join(",")}`,
    );
  };

  const rerunSelected = () => {
    const only = selected.join(",");
    navigate(
      `${paths.dashboard.simulate.simulationRun(env.id, protoRunId(env.id, Date.now().toString(36)))}?only=${only}`,
    );
  };
  const bands = evalId ? distributionFor(comparison, evalId) : [];
  const peak = Math.max(1, ...bands.flatMap((b) => b.counts));
  const rowPad = ROW_HEIGHTS.find((h) => h.id === view.rowHeight)?.py ?? 2;

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      {/* ── header ──
          Two rows on purpose. Everything was on one line — title, subtitle,
          five chips, four controls and two icons — and a row that dense reads
          as a toolbar someone kept appending to. The top line says what you
          are looking at; the second says what you can do to it. */}
      <Box sx={{ borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2, pt: 1.5, pb: 1 }}>
          <IconButton size="small" onClick={back}>
            <Iconify icon="eva:arrow-ios-back-fill" width={18} sx={{ color: "text.subtitle" }} />
          </IconButton>
          <Box minWidth={0}>
            <Typography noWrap sx={{ typography: "m2", fontWeight: 600 }}>{env.name}</Typography>
            {/* The claim this screen rests on, stated only as far as it is
                true. "Identical in every run" was printed unconditionally, and
                the moment someone compared a full sweep against a re-run of
                four blockers it was a lie in the subtitle of the screen that
                decides releases. */}
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {comparison.runs.length} agent versions · {comparison.coverage.shared} scenarios in every run
              {comparison.coverage.partial > 0 && ` · ${comparison.coverage.partial} in only some`}
              {comparison.coverage.unmeasured > 0 && ` · ${comparison.coverage.unmeasured} not measured`}
              {sampleNote}
            </Typography>
          </Box>
          <Box flex={1} />
          <Tooltip arrow title="Export comparison">
            <IconButton size="small" onClick={exportComparison}>
              <Iconify icon="solar:download-minimalistic-linear" width={16} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
          <Tooltip arrow title="Copy link">
            <IconButton
              size="small"
              onClick={() => {
                navigator.clipboard?.writeText(window.location.href);
                enqueueSnackbar("Link copied", { variant: "success" });
              }}
            >
              <Iconify icon="solar:share-linear" width={16} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
        </Stack>

        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{ px: 2, pb: 1.5, flexWrap: "wrap", rowGap: 1 }}
        >
          {comparison.runs.map((r, i) => (
            <Stack
              key={r.id}
              direction="row" alignItems="center" spacing={0.75}
              sx={{
                pl: 1, pr: comparison.runs.length > 2 ? 0.25 : 1, py: 0.5,
                borderRadius: 0.875, border: "1px solid",
                borderColor: alpha(r.color, 0.35),
                bgcolor: (t) => alpha(r.color, t.palette.mode === "dark" ? 0.12 : 0.06),
              }}
            >
              <Letter letter={r.letter} color={r.color} />
              {/*
                Both halves of the pairing on the pill — a comparison is
                `env × agent`, and reading only "agent v2" hides "which
                world did that number come from". Env part is muted so
                the label stays scannable when both are the same.
              */}
              <Typography noWrap sx={{ typography: "s3", fontWeight: 600, maxWidth: 180 }}>
                agent {r.agentVersion}
                {r.envVersion && (
                  <Box component="span" sx={{ color: "text.subtitle", fontWeight: 500 }}>
                    {" "}× env {r.envVersion}
                  </Box>
                )}
                {" "}· {r.passRate}%
              </Typography>
              {i === 0 && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>baseline</Typography>}
              {comparison.runs.length > 2 && (
                <Tooltip arrow title="Remove from this comparison">
                  <IconButton size="small" onClick={() => setRuns(runIds.filter((id) => id !== r.id))}>
                    <Iconify icon="mingcute:close-line" width={12} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Tooltip>
              )}
            </Stack>
          ))}
          {others.length > 0 && (
            <Button
              size="small"
              onClick={(e) => setAddAnchor(e.currentTarget)}
              startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: 700, color: "text.secondary", border: "1px dashed", borderColor: "divider" }}
            >
              Add run
            </Button>
          )}

          <Box flex={1} />

          <SavedViews
            views={savedViews}
            activeId={activeViewId}
            dirty={dirty}
            onApply={applyView}
            onSave={saveView}
            onUpdate={updateView}
            onRename={renameView}
            onDelete={deleteView}
          />

          <CompareActions
            filters={filters} onFilters={setFilters} evals={comparison.evals}
            view={view} onView={setViewOverride}
            onSaveDefault={() => {
              patch({ compareView: view });
              enqueueSnackbar("Saved as the default view for this environment", { variant: "success" });
            }}
            onResetView={() => { setViewOverride(defaultView()); patch({ compareView: null }); }}
            selectedCount={selected.length}
            onExport={exportComparison}
            onCopyLink={() => {
              navigator.clipboard?.writeText(window.location.href);
              enqueueSnackbar("Link copied", { variant: "success" });
            }}
            onRerun={rerunSelected}
            onRegrade={() => enqueueSnackbar(
              `Re-grading ${selected.length} scenario${selected.length === 1 ? "" : "s"} from recorded evidence — no calls are made`,
              { variant: "info" },
            )}
            onOptimize={optimizeSelected}
          />
        </Stack>
      </Box>

      <Menu open={!!addAnchor} anchorEl={addAnchor} onClose={() => setAddAnchor(null)}>
        {others.map((r) => (
          <MenuItem
            key={r.id}
            sx={{ typography: "s2" }}
            onClick={() => { setRuns([...runIds, r.id]); setAddAnchor(null); }}
          >
            <Letter letter={r.letter} color={r.color} />
            <Box component="span" sx={{ ml: 1 }}>
              Run {r.ordinal} · agent {r.agentVersion}
              {r.envVersion && (
                <Box component="span" sx={{ color: "text.subtitle" }}>
                  {" "}× env {r.envVersion}
                </Box>
              )}
              {" "}· {r.passRate}%
            </Box>
          </MenuItem>
        ))}
      </Menu>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        {/* One card for both. The distribution is a summary of the rows
            underneath it, so a second border and a gap between them spends
            space on a boundary that is not real. */}
        <Box
          sx={{
            border: "1px solid", borderColor: "divider", borderRadius: 1.5,
            bgcolor: "background.paper", overflow: "hidden",
          }}
        >
        {view.showChart && (
          <SectionCard
            title="Score distribution"
            sx={{ border: "none", borderRadius: 0, bgcolor: "transparent" }}
            action={
              <Stack direction="row" alignItems="center" spacing={2}>
                <Stack direction="row" spacing={1.5} sx={{ display: { xs: "none", md: "flex" } }}>
                  {comparison.runs.map((r) => (
                    <Stack key={r.id} direction="row" alignItems="center" spacing={0.625}>
                      <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: r.color }} />
                      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                        agent {r.agentVersion}
                        {r.envVersion && (
                          <Box component="span" sx={{ color: "text.subtitle" }}>
                            {" "}× env {r.envVersion}
                          </Box>
                        )}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              <TextField
                select size="small" value={evalId || ""}
                onChange={(e) => setPickedEval(e.target.value)}
                sx={{ minWidth: 190, "& .MuiInputBase-input": { typography: "s2", py: 0.75 } }}
              >
                {comparison.evals.map((e) => (
                  <MenuItem key={e.id} value={e.id} sx={{ typography: "s2" }}>{e.name}</MenuItem>
                ))}
              </TextField>
              </Stack>
            }
          >
            <Box sx={{ px: 1.5, pt: 0.5, pb: 0 }}>
              <ReactApexChart
                type="bar"
                height={140}
                series={comparison.runs.map((r, i) => ({
                  name: `${r.letter} · ${r.agentVersion}`,
                  data: bands.map((b) => b.counts[i]),
                }))}
                options={{
                  chart: { toolbar: { show: false }, fontFamily: theme.typography.fontFamily, background: "transparent" },
                  theme: { mode: theme.palette.mode },
                  colors: comparison.runs.map((r) => r.color),
                  plotOptions: { bar: { columnWidth: "48%", borderRadius: 2 } },
                  dataLabels: { enabled: false },
                  legend: { show: false },
                  grid: { borderColor: theme.palette.divider, strokeDashArray: 4, xaxis: { lines: { show: false } } },
                  xaxis: {
                    categories: bands.map((b) => b.label),
                    axisBorder: { show: false }, axisTicks: { show: false },
                    labels: { style: { colors: theme.palette.text.secondary, fontSize: "11px" } },
                  },
                  yaxis: {
                    min: 0, max: peak, tickAmount: Math.min(peak, 3),
                    labels: { style: { colors: theme.palette.text.secondary, fontSize: "11px" }, formatter: (v) => `${Math.round(v)}` },
                  },
                  tooltip: { y: { formatter: (v) => `${v} scenarios` } },
                }}
              />
            </Box>
          </SectionCard>
        )}

        {/* Title, count, search and the diff switch on one line — they were
            three stacked bands saying one thing. */}
        <Stack
          direction="row" alignItems="center" spacing={2}
          sx={{ px: 2.5, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>Scenario by scenario</Typography>
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
              {changed} of {comparison.rows.length} ended differently · showing {rows.length}
            </Typography>
          </Box>
          <CompareSearchBar
            query={query} onQuery={setQuery}
            view={view} onView={setViewOverride}
          />
        </Stack>

          {rows.length === 0 ? (
            <EmptyState
              icon="solar:filter-linear"
              title="No scenarios match"
              body="Loosen the filters, or clear the search."
            />
          ) : view.view === "summary" ? (
            <CompareSummary comparison={comparison} evals={comparison.evals} />
          ) : view.view === "grid" ? (
            <CompareGrid
              comparison={comparison}
              groups={groups}
              evals={comparison.evals}
              envState={envState}
              view={view}
              onOpen={setOpenRow}
            />
          ) : (
            <TableView
              groups={groups}
              view={view}
              rowPad={rowPad}
              selected={selected}
              onToggle={(id) => setSelected((prev) => (
                prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
              ))}
              onOpen={setOpenRow}
            />
          )}
        </Box>
      </Box>

      {/* Opening a row is not a peek at a cell — it is the call, in every run,
          with the same Show Diff the table is already reading under. */}
      <CallCompare
        open={!!openRow}
        row={openRow}
        rows={rows}
        runs={comparison.runs}
        env={env}
        envState={envState}
        diff={view.diff}
        onDiff={(on) => setViewOverride({ ...view, diff: on })}
        onOpenRow={setOpenRow}
        onClose={() => setOpenRow(null)}
      />
    </Stack>
  );
}

/* ── table view ──────────────────────────────────────────────────────────── */

function TableView({ groups, view, rowPad, selected, onToggle, onOpen }) {
  return (
    <>
      <Box
        sx={{
          display: { xs: "none", lg: "grid" },
          gridTemplateColumns: "36px 300px 1fr 190px",
          columnGap: 2,
          px: 2.5, py: 1, borderBottom: "1px solid", borderColor: "divider",
        }}
      >
        <Box />
        <ColHead>Scenario</ColHead>
        <ColHead>{view.diff ? "What changed against the baseline" : "What each run did with it"}</ColHead>
        <ColHead sx={{ textAlign: "right" }}>
          {[view.columns.duration && "Duration", view.columns.tokens && "Tokens", view.columns.cost && "Cost"]
            .filter(Boolean).join(" · ")}
        </ColHead>
      </Box>

      {groups.map((group) => (
        <Box key={group.id}>
          {group.label && (
            <Stack
              direction="row" alignItems="center" spacing={1}
              sx={{ px: 2.5, py: 1, bgcolor: "background.neutral", borderBottom: "1px solid", borderColor: "divider" }}
            >
              <Typography sx={{ typography: "s3", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4 }}>
                {group.label}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{group.rows.length}</Typography>
            </Stack>
          )}

          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {group.rows.map((row) => (
              <Box
                key={row.id}
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", lg: "36px 300px 1fr 190px" },
                  columnGap: 2, px: 2.5, py: rowPad,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover", "& .row-open": { opacity: 1 } },
                }}
                onClick={() => onOpen(row)}
              >
                <Box
                  sx={{ display: "flex", alignItems: "flex-start", pt: 0.25 }}
                  onClick={(e) => { e.stopPropagation(); onToggle(row.id); }}
                >
                  <Checkbox size="small" checked={selected.includes(row.id)} readOnly tabIndex={-1} sx={{ p: 0.5, pointerEvents: "none" }} />
                </Box>

                <Box minWidth={0}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    {row.fixed && <Movement kind="fixed" />}
                    {row.broke && <Movement kind="broke" />}
                    <Typography sx={{ typography: "s2", fontWeight: 700 }}>{row.title}</Typography>
                  </Stack>
                  {view.rowHeight !== "compact" && (
                    <Typography
                      sx={{
                        typography: "s3", color: "text.subtitle", mt: 0.25,
                        display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden",
                      }}
                    >
                      {row.task}
                    </Typography>
                  )}
                  {view.rowHeight === "large" && (
                    <Box sx={{ mt: 0.75 }}><PersonaBadge persona={row.persona} compact /></Box>
                  )}
                </Box>

                <Stack spacing={1.25} sx={{ minWidth: 0 }}>
                  {row.cells.map((c, i) => (
                    <Stack key={c.runId} direction="row" spacing={1.25} alignItems="flex-start">
                      <Letter letter={c.letter} color={c.color} />
                      <Box sx={{ width: 78, flexShrink: 0 }}><Verdict status={c.status} passes={c.passes} repeats={c.repeats} /></Box>
                      {/* Who the failure belongs to, on the row that reports
                          it — an unmeasured row must never read as the agent's. */}
                      {c.domain && !c.domain.measured && <DomainChip domain={c.domain} dense />}
                      <Typography
                        sx={{
                          flex: 1, minWidth: 0, typography: "s2", color: "text.secondary",
                          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden",
                        }}
                      >
                        {view.diff && i > 0
                          ? <DiffText diff={behaviourDiff(row.cells[0].task, c.task)} />
                          : (
                            <>
                              {c.deciding.kind === "claim" && (
                                <Box component="span" sx={{ color: "#C2603F", fontWeight: 700 }}>Said, not done — </Box>
                              )}
                              {c.deciding.text}
                            </>
                          )}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>

                <Stack spacing={1.25} sx={{ display: { xs: "none", lg: "flex" }, position: "relative" }}>
                  {/* The row opens a side-by-side of the transcripts; without a
                      mark, nothing on it says that. */}
                  <Iconify
                    icon="eva:arrow-ios-forward-fill"
                    width={16}
                    className="row-open"
                    sx={{
                      position: "absolute", right: -14, top: 2, opacity: 0,
                      transition: "opacity .12s", color: "text.subtitle", pointerEvents: "none",
                    }}
                  />
                  {row.cells.map((c) => (
                    <Stack key={c.runId} direction="row" spacing={1.5} justifyContent="flex-end">
                      {view.columns.duration && (
                        <Metric value={c.task ? `${(c.durationMs / 1000).toFixed(1)}s` : "—"} delta={c.durationDelta} lowerIsBetter />
                      )}
                      {view.columns.tokens && (
                        <Metric value={c.task ? `${c.tokens}` : "—"} delta={c.tokensDelta} lowerIsBetter />
                      )}
                      {view.columns.cost && <Metric value={`$${(c.cost || 0).toFixed(3)}`} />}
                    </Stack>
                  ))}
                </Stack>
              </Box>
            ))}
          </Stack>
        </Box>
      ))}
    </>
  );
}
TableView.propTypes = {
  groups: PropTypes.array, view: PropTypes.object, rowPad: PropTypes.number,
  selected: PropTypes.array, onToggle: PropTypes.func, onOpen: PropTypes.func,
};

/** The behaviour diff, in one line. */
function DiffText({ diff }) {
  if (!diff) return null;
  if (diff.identical) {
    return <Box component="span" sx={{ color: "text.subtitle" }}>Same behaviour as the baseline.</Box>;
  }
  const parts = [];
  if (diff.missedNow.length) parts.push(`stopped calling ${diff.missedNow.join(", ")}`);
  else if (diff.dropped.length) parts.push(`dropped ${diff.dropped.join(", ")}`);
  if (diff.added.length) parts.push(`added ${diff.added.join(", ")}`);
  if (diff.turnDelta) parts.push(`${Math.abs(diff.turnDelta)} ${diff.turnDelta > 0 ? "more" : "fewer"} turns`);
  if (!parts.length && diff.firstDivergence >= 0) {
    parts.push(`same tools, wording differs from turn ${diff.firstDivergence + 1}`);
  }
  return (
    <>
      {diff.verdictChanged && (
        <Box component="span" sx={{ color: "#C2603F", fontWeight: 700 }}>Verdict changed — </Box>
      )}
      {parts.join(", ")}.
    </>
  );
}
DiffText.propTypes = { diff: PropTypes.object };


function ColHead({ children, sx }) {
  return (
    <Typography
      noWrap
      sx={{
        typography: "s3", fontWeight: 700, color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.3, ...sx,
      }}
    >
      {children}
    </Typography>
  );
}
ColHead.propTypes = { children: PropTypes.node, sx: PropTypes.object };

function Letter({ letter, color }) {
  return (
    <Box
      sx={{
        width: 20, height: 20, borderRadius: 0.75, flexShrink: 0,
        display: "grid", placeItems: "center", typography: "s3", fontWeight: 700,
        color, bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.22 : 0.14),
      }}
    >
      {letter}
    </Box>
  );
}
Letter.propTypes = { letter: PropTypes.string, color: PropTypes.string };

function Movement({ kind }) {
  const fixed = kind === "fixed";
  const color = fixed ? "#5AA47B" : "#C2603F";
  return (
    <Tooltip arrow title={fixed ? "Failed on the baseline, passes on a later run" : "Passed on the baseline, fails on a later run"}>
      <Typography
        sx={{
          px: 0.75, py: 0.125, borderRadius: 0.5, flexShrink: 0,
          typography: "s3", fontWeight: 700, color,
          bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.18 : 0.1),
        }}
      >
        {fixed ? "FIXED" : "BROKE"}
      </Typography>
    </Tooltip>
  );
}
Movement.propTypes = { kind: PropTypes.string };

function Metric({ value, delta, lowerIsBetter }) {
  const good = delta == null ? null : lowerIsBetter ? delta < 0 : delta > 0;
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.5} sx={{ minWidth: 78, justifyContent: "flex-end" }}>
      <Typography sx={{ typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      {delta != null && delta !== 0 && (
        <Typography sx={{ typography: "s3", fontWeight: 600, color: good ? "#5AA47B" : "#C2603F" }}>
          {delta > 0 ? "+" : ""}{delta}%
        </Typography>
      )}
    </Stack>
  );
}
Metric.propTypes = { value: PropTypes.string, delta: PropTypes.number, lowerIsBetter: PropTypes.bool };
