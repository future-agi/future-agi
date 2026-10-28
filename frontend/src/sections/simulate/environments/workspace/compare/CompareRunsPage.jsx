import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { LoadingScreen } from "src/components/loading-screen";
import { paths } from "src/routes/paths";
import { pageBackButtonSx } from "src/sections/settings/integrations/styles";
import { useEnvironment } from "src/api/simulate-environments/environment";
import { useEnvironmentRuns } from "src/api/simulate-environments/runs";

import { useEnvState } from "../../store/envState";
import { BUILD_STATUS } from "../../myEnvironments.constants";
import EmptyState from "../../components/EmptyState";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import WorkspaceErrorBoundary from "../WorkspaceErrorBoundary";
import RunPills from "./RunPills";
import AddRunMenu from "./AddRunMenu";
import CompareActions, {
  CompareSearchBar,
  SavedViews,
} from "./CompareControls";
import CompareTable from "./CompareTable";
import CompareGrid from "./CompareGrid";
import CompareSummary from "./CompareSummary";
import CompareVerdict from "./CompareVerdict";
import ScoreDistribution from "./ScoreDistribution";
import CallCompareDrawer from "./CallCompareDrawer";
import { readComparison } from "./compareStats";
import { CALL_CAP, useComparison } from "./useComparison";
import { comparisonPayload, downloadComparison } from "./exportComparison";
import { coverageFromRuns, subtitleText, withIdentity } from "./compareFormat";
import {
  ROW_HEIGHTS,
  applyQuick,
  bandRows,
  defaultView,
  searchRows,
  snapshotsEqual,
  sortRows,
  viewSnapshot,
} from "./compareView";

// The most runs the page reads for its pills and the Add run menu.
const RUN_CAP = 200;

const ICON_SX = { color: "text.subtitle" };

function CountChip({ count, label, color }) {
  return (
    <Typography
      component="span"
      sx={{
        px: 0.75,
        py: 0.125,
        borderRadius: "4px",
        typography: "s3",
        fontWeight: "fontWeightSemiBold",
        color: count ? color : "text.subtitle",
        bgcolor: (t) =>
          alpha(
            count
              ? t.palette[color.split(".")[0]].main
              : t.palette.text.primary,
            0.08,
          ),
      }}
    >
      {`${count} ${label}`}
    </Typography>
  );
}
CountChip.propTypes = {
  count: PropTypes.number.isRequired,
  label: PropTypes.string.isRequired,
  color: PropTypes.string.isRequired,
};

/**
 * Two or more runs of one environment, side by side.
 *
 * `?runs=a,b` names them; the first is the baseline and everything else is
 * read against it. The header says what is being compared and how far the
 * runs are the same measurement; the body reads them scenario by scenario.
 */
function CompareRuns() {
  const { envId } = useParams();
  const navigate = useNavigate();
  const from = useLocation().state?.back || null;
  const [params, setParams] = useSearchParams();
  const [addAnchor, setAddAnchor] = useState(null);
  const [query, setQuery] = useState("");
  const [openRow, setOpenRow] = useState(null);

  const { env, bootstrapState, notFound, error, refetch, isLoading } =
    useEnvironment(envId);
  // Seed the store only from a finished build, as the workspace does.
  const building =
    env?.buildStatus === BUILD_STATUS.BUILDING ||
    env?.buildStatus === BUILD_STATUS.FAILED;
  const { envState } = useEnvState(
    envId,
    building ? undefined : bootstrapState,
  );
  const {
    runs: envRuns,
    count,
    isLoading: runsLoading,
  } = useEnvironmentRuns(env, envState, { pageSize: RUN_CAP });

  const runIds = useMemo(
    () => (params.get("runs") || "").split(",").filter(Boolean),
    [params],
  );
  const byId = useMemo(
    () => new Map(envRuns.map((r) => [r.id, withIdentity(r)])),
    [envRuns],
  );
  const runs = useMemo(
    () => runIds.map((id) => byId.get(id)).filter(Boolean),
    [runIds, byId],
  );
  const others = useMemo(
    () => envRuns.filter((r) => !runIds.includes(r.id)).map(withIdentity),
    [envRuns, runIds],
  );
  const comparedIds = useMemo(
    () => (runs.length >= 2 ? runs.map((r) => r.id) : []),
    [runs],
  );
  const comparison = useComparison(envId, comparedIds);
  const { rows: comparedRows, isLoading: callsLoading } = comparison;
  const read = useMemo(
    () =>
      callsLoading ? null : readComparison({ rows: comparedRows }, comparedIds),
    [callsLoading, comparedRows, comparedIds],
  );

  // Views can't be saved yet: the reading lasts for this visit, and a view
  // named in the URL that isn't there opens the default reading.
  const [savedViews, setSavedViews] = useState([]);
  const [viewOverride, setViewOverride] = useState(null);
  const activeViewId = params.get("view") || null;
  const activeView = savedViews.find((v) => v.id === activeViewId) || null;
  const view =
    viewOverride ||
    (activeView
      ? { ...defaultView(), ...activeView.config.view }
      : defaultView());
  const dirty = !snapshotsEqual(
    viewSnapshot(view),
    activeView ? activeView.config : viewSnapshot(defaultView()),
  );

  const setActiveView = (id) => {
    const next = new URLSearchParams(params);
    if (id) next.set("view", id);
    else next.delete("view");
    setParams(next, { replace: true });
  };
  const applyView = (id) => {
    const v = savedViews.find((x) => x.id === id);
    setViewOverride(v ? { ...defaultView(), ...v.config.view } : defaultView());
    setActiveView(id);
  };
  const updateView = (id) => {
    setSavedViews(
      savedViews.map((v) =>
        v.id === id ? { ...v, config: viewSnapshot(view) } : v,
      ),
    );
    enqueueSnackbar("View updated", { variant: "success" });
  };
  const renameView = (id, name) =>
    setSavedViews(savedViews.map((v) => (v.id === id ? { ...v, name } : v)));
  const deleteView = (id) => {
    setSavedViews(savedViews.filter((v) => v.id !== id));
    if (id === activeViewId) setActiveView(null);
  };

  const rows = useMemo(
    () =>
      sortRows(
        applyQuick(searchRows(comparison.rows, query), view.quick),
        view.sort,
        comparedIds,
      ),
    [comparison.rows, query, view.quick, view.sort, comparedIds],
  );
  const groups = useMemo(
    () =>
      bandRows(rows, {
        group: view.group,
        sort: view.sort,
        runIds: comparedIds,
      }),
    [rows, view.group, view.sort, comparedIds],
  );

  const back = () =>
    from?.to
      ? navigate(from.to, { state: from.state })
      : navigate(
          paths.dashboard.simulate.environments.workspaceTab(envId, "runs"),
        );

  const setRuns = (ids) => {
    const next = new URLSearchParams(params);
    next.set("runs", ids.join(","));
    setParams(next, { replace: true });
  };

  const exportComparison = () =>
    downloadComparison(
      comparisonPayload({
        env,
        runs,
        comparison: comparison.isLoading ? null : { rows },
        read,
      }),
    );

  const copyLink = () => {
    navigator.clipboard?.writeText(window.location.href);
    enqueueSnackbar("Link copied", { variant: "success" });
  };

  if (isLoading || (env && runsLoading)) return <LoadingScreen />;

  const backButton = (
    <Button
      variant="outlined"
      size="small"
      onClick={back}
      sx={pageBackButtonSx}
    >
      {from?.label ? `Back to ${from.label}` : "Back to runs"}
    </Button>
  );

  if (!env) {
    return (
      <EmptyState
        icon="solar:danger-triangle-linear"
        title={
          notFound ? "Environment not found" : "Couldn't load this environment"
        }
        body={notFound ? null : error?.message}
        action={
          notFound ? (
            backButton
          ) : (
            <Button variant="outlined" size="small" onClick={() => refetch?.()}>
              Retry
            </Button>
          )
        }
      />
    );
  }

  if (runs.length < 2) {
    // Say what is missing; never draw a comparison of runs that don't exist.
    const finished = envRuns.filter((r) => r.finishedAt).length;
    return (
      <EmptyState
        icon="solar:transfer-horizontal-linear"
        title={finished < 2 ? "Not yet run" : "Nothing to compare"}
        body={
          finished < 2
            ? `Comparing needs two finished runs. This environment has ${finished}. Run it, then compare.`
            : "Pick two or more runs on the Runs tab and press Compare."
        }
        action={backButton}
      />
    );
  }

  const coverage = comparison.rows.length
    ? comparison.coverage
    : coverageFromRuns(runs);
  // The calls are in, and none of them failed to load.
  const callsReady = !comparison.isLoading && !comparison.error;
  const showChart = view.showChart && callsReady && comparison.evals.length > 0;
  const rowPad = ROW_HEIGHTS.find((h) => h.id === view.rowHeight)?.py ?? 1.5;
  const runLabel = (id) => runs.find((r) => r.id === id)?.label ?? id;

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Box
        sx={{
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 2, pt: 1.5, pb: 1 }}
        >
          {backButton}
          <Box
            sx={{ height: 20, width: "1px", bgcolor: "divider", flexShrink: 0 }}
          />
          <Box minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                noWrap
                sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}
              >
                {env.name}
              </Typography>
              <UiOnlyFlag feature="compareRuns" />
            </Stack>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {subtitleText(runs, coverage)}
            </Typography>
          </Box>
          <Box flex={1} />
          <CustomTooltip show arrow size="small" title="Export comparison">
            <IconButton
              size="small"
              aria-label="Export comparison"
              onClick={exportComparison}
            >
              <Iconify
                icon="solar:download-minimalistic-linear"
                width={16}
                sx={ICON_SX}
              />
            </IconButton>
          </CustomTooltip>
          <CustomTooltip show arrow size="small" title="Copy link">
            <IconButton size="small" aria-label="Copy link" onClick={copyLink}>
              <Iconify icon="solar:share-linear" width={16} sx={ICON_SX} />
            </IconButton>
          </CustomTooltip>
        </Stack>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 2, pb: 1.5, flexWrap: "wrap", rowGap: 1 }}
        >
          <RunPills
            runs={runs}
            onRemove={(id) => setRuns(runIds.filter((x) => x !== id))}
          />
          <Button
            size="small"
            onClick={(e) => setAddAnchor(e.currentTarget)}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            sx={{
              color: "text.secondary",
              border: "1px dashed",
              borderColor: "divider",
            }}
          >
            Add run
          </Button>
          <Box flex={1} />
          <SavedViews
            views={savedViews}
            activeId={activeView ? activeViewId : null}
            dirty={dirty}
            onApply={applyView}
            onUpdate={updateView}
            onRename={renameView}
            onDelete={deleteView}
          />
          <CompareActions
            view={view}
            onView={setViewOverride}
            onResetView={() => setViewOverride(defaultView())}
            onExport={exportComparison}
            onCopyLink={copyLink}
          />
        </Stack>
      </Box>

      <AddRunMenu
        anchorEl={addAnchor}
        onClose={() => setAddAnchor(null)}
        runs={others}
        capped={count > RUN_CAP}
        onAdd={(ids) => setRuns([...runIds, ...ids])}
      />

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        {comparison.cappedRuns.map(({ runId, count: calls }) => (
          <Typography
            key={runId}
            sx={{ typography: "s2", color: "warning.main", mb: 1 }}
          >
            {`${runLabel(runId)} has ${calls.toLocaleString()} calls. Comparing the first ${CALL_CAP.toLocaleString()}.`}
          </Typography>
        ))}

        {callsReady && <CompareVerdict read={read} runs={runs} />}

        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            overflow: "hidden",
          }}
        >
          {showChart && (
            <ScoreDistribution
              rows={comparison.rows}
              runs={runs}
              evals={comparison.evals}
            />
          )}
          <Stack
            direction="row"
            alignItems="center"
            spacing={2}
            sx={{
              px: 2.5,
              py: 1.25,
              borderTop: showChart ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography
                  sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
                >
                  Scenario by scenario
                </Typography>
                {callsReady && (
                  <>
                    <CountChip
                      count={comparison.flips.fixed}
                      label="fixed"
                      color="success.main"
                    />
                    <CountChip
                      count={comparison.flips.regressed}
                      label="regressed"
                      color="error.main"
                    />
                  </>
                )}
              </Stack>
              {callsReady && (
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.subtitle" }}
                >
                  {`${comparison.changed} of ${comparison.rows.length} ended differently`}
                  {comparison.flips.regressed
                    ? ` · ${comparison.flips.regressed} passed → failed`
                    : ""}
                  {` · showing ${rows.length}`}
                </Typography>
              )}
            </Box>
            <CompareSearchBar query={query} onQuery={setQuery} />
          </Stack>

          <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
            {comparison.isLoading ? (
              <Stack alignItems="center" spacing={1} sx={{ py: 6 }}>
                <CircularProgress size={20} />
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  Loading calls
                </Typography>
              </Stack>
            ) : comparison.error ? (
              <EmptyState
                icon="solar:danger-triangle-linear"
                title="Couldn't load the calls"
                body={comparison.error.message}
                action={
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={comparison.refetch}
                  >
                    Retry
                  </Button>
                }
              />
            ) : rows.length === 0 ? (
              <EmptyState
                icon="solar:filter-linear"
                title={
                  comparison.rows.length ? "No scenarios match" : "No calls yet"
                }
                body={
                  comparison.rows.length
                    ? "Loosen the filters, or clear the search."
                    : "These runs have no calls to compare."
                }
              />
            ) : view.view === "summary" ? (
              <CompareSummary comparison={comparison} runs={runs} />
            ) : view.view === "grid" ? (
              <CompareGrid
                groups={groups}
                runs={runs}
                runStats={comparison.runStats}
                evals={comparison.evals}
                view={view}
                onOpen={setOpenRow}
              />
            ) : (
              <CompareTable
                groups={groups}
                runs={runs}
                evals={comparison.evals}
                view={view}
                rowPad={rowPad}
                onOpen={setOpenRow}
              />
            )}
          </Box>
        </Box>
      </Box>

      <CallCompareDrawer
        row={openRow}
        rows={rows}
        runs={runs}
        onOpenRow={setOpenRow}
        onClose={() => setOpenRow(null)}
      />
    </Stack>
  );
}

export default function CompareRunsPage() {
  return (
    <WorkspaceErrorBoundary>
      <CompareRuns />
    </WorkspaceErrorBoundary>
  );
}
