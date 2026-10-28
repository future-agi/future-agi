import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  InputBase,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import Iconify from "src/components/iconify";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { useRouter } from "src/routes/hooks";
import { paths } from "src/routes/paths";
import {
  VIEWER_CANT_RUN,
  useCanStartRuns,
} from "src/sections/projects/runPermission";
import AskPanel from "./AskPanel";
import CategoryExplorer from "./CategoryExplorer";
import CategoryTable from "./CategoryTable";
import ChangeCards from "./ChangeCards";
import EditDrawer from "./EditDrawer";
import ExamplePanel from "./ExamplePanel";
import Findings from "./Findings";
import Highlights from "./Highlights";
import MethodNote from "./MethodNote";
import RerunDialog from "./RerunDialog";
import RunProgress from "./RunProgress";
import TrendCard from "./TrendCard";
import {
  deleteConfig,
  failedRunNext,
  forgetConfig,
  insightsErrorMessage,
  insightsKeys,
  patchConfig,
  projectMissing,
  runFailureReason,
  useInsightsConfig,
  useInsightsRun,
} from "./insightsApi";
import { buildSegments, resultTops } from "./insightsDistribution";
import { formatCount, runOutcome } from "./insightsFormat";
import { useCancellable } from "./insightsHooks";
import { isRunActive, lensFor, unitTitle } from "./insightsLenses";
import { reportMetaParts } from "./insightsReport";
import { ReportProvider, useReportModel } from "./reportContext";
import { ErrorState } from "./parts";

const CONTENT_WIDTH = 1180;

function HeaderChip({ label, icon, title }) {
  const chip = (
    <Chip
      size="small"
      variant="outlined"
      icon={icon ? <Iconify icon={icon} width={13} /> : undefined}
      label={label}
      sx={{
        height: 22,
        borderRadius: 0.75,
        fontSize: 12,
        maxWidth: 320,
        color: "text.secondary",
        "& .MuiChip-icon": { color: "text.secondary", ml: 0.75 },
      }}
    />
  );
  return title ? (
    <Tooltip describeChild arrow title={title}>
      {chip}
    </Tooltip>
  ) : (
    chip
  );
}

HeaderChip.propTypes = {
  label: PropTypes.node.isRequired,
  icon: PropTypes.string,
  title: PropTypes.node,
};

// The report's name, renamed in place: click it (or the pencil), Enter saves,
// Escape cancels.
function InlineName({ config }) {
  const queryClient = useQueryClient();
  const canRun = useCanStartRuns();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(config.name);
  const cancelled = useRef(false);
  const saving = useCancellable("The report could not be renamed.");
  const save = () =>
    saving.run(async () => {
      const name = value.trim();
      if (cancelled.current || !name || name === config.name) {
        cancelled.current = false;
        setValue(config.name);
        setEditing(false);
        return null;
      }
      const updated = await patchConfig(config.id, {
        name: name.slice(0, 255),
      });
      queryClient.setQueryData(insightsKeys.config(config.id), (current) =>
        current ? { ...current, name: updated?.name || name } : current,
      );
      queryClient.invalidateQueries({ queryKey: ["insights", "configs"] });
      setEditing(false);
      return updated;
    });
  if (editing)
    return (
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <InputBase
          autoFocus
          fullWidth
          value={value}
          onChange={(event) => setValue(event.target.value.slice(0, 255))}
          onBlur={save}
          onKeyDown={(event) => {
            // Enter commits through blur, so the name is saved once.
            if (event.key === "Enter") event.currentTarget.blur();
            if (event.key === "Escape") {
              cancelled.current = true;
              setValue(config.name);
              setEditing(false);
            }
          }}
          inputProps={{ "aria-label": "Report name" }}
          disabled={saving.state.status === "busy"}
          sx={{
            fontSize: 18,
            fontWeight: 600,
            px: 0.75,
            ml: -0.75,
            borderRadius: 0.75,
            outline: "1px solid",
            outlineColor: "primary.main",
          }}
        />
        {saving.state.status === "error" && (
          <Typography variant="caption" color="error.main">
            {saving.state.error}
          </Typography>
        )}
      </Box>
    );
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.25}
      sx={{ minWidth: 0, "&:hover .rename": { opacity: 1 } }}
    >
      <Typography
        variant="m2"
        component="h1"
        fontWeight={600}
        noWrap
        title={canRun ? `${config.name} · click to rename` : config.name}
        onClick={() => {
          if (!canRun) return;
          setValue(config.name);
          setEditing(true);
        }}
        sx={{ cursor: canRun ? "text" : "default", minWidth: 0 }}
      >
        {config.name}
      </Typography>
      <IconButton
        className="rename"
        size="small"
        aria-label="Rename report"
        disabled={!canRun}
        onClick={() => {
          setValue(config.name);
          setEditing(true);
        }}
        sx={{
          opacity: 0.4,
          color: "text.secondary",
          "&:focus-visible": { opacity: 1 },
        }}
      >
        <Iconify icon="mdi:pencil-outline" width={14} />
      </IconButton>
    </Stack>
  );
}

InlineName.propTypes = { config: PropTypes.object.isRequired };

function ReportHeader({
  config,
  run,
  busy,
  onRunNow,
  onEdit,
  onRediscover,
  onDelete,
  onDetails,
}) {
  const [anchor, setAnchor] = useState(null);
  const canRun = useCanStartRuns();
  const lens = lensFor(run?.settings?.lens || config.lens);
  const scope = run?.scope?.label || config.latest_run?.scope?.label;
  return (
    <Stack
      direction="row"
      spacing={2}
      alignItems="flex-start"
      justifyContent="space-between"
    >
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <InlineName key={config.name} config={config} />
        <Stack
          direction="row"
          spacing={0.75}
          alignItems="center"
          useFlexGap
          flexWrap="wrap"
          sx={{ mt: 0.75 }}
        >
          <HeaderChip
            label={unitTitle(config.unit)}
            icon="mdi:layers-outline"
          />
          <HeaderChip
            label={lens.key === "custom" ? "Custom question" : lens.title}
            icon={lens.icon}
          />
          {scope && (
            <HeaderChip
              label={scope}
              icon="mdi:calendar-range"
              title="What this run read"
            />
          )}
          {run && (
            <Box
              component="button"
              type="button"
              onClick={onDetails}
              sx={{
                ml: 0.5,
                p: 0,
                border: 0,
                bgcolor: "transparent",
                font: "inherit",
                fontSize: 12,
                color: "text.secondary",
                cursor: "pointer",
                textDecoration: "underline",
                textDecorationColor: "transparent",
                "&:hover": {
                  color: "text.primary",
                  textDecorationColor: "inherit",
                },
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                },
              }}
            >
              Details
            </Box>
          )}
        </Stack>
        {!canRun && (
          <Typography
            variant="caption"
            color="text.secondary"
            component="p"
            sx={{ mt: 0.75 }}
          >
            {VIEWER_CANT_RUN}
          </Typography>
        )}
      </Box>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{ flexShrink: 0 }}
      >
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          startIcon={<Iconify icon="mdi:play-outline" width={16} />}
          onClick={onRunNow}
          disabled={busy || !canRun}
          sx={{ fontSize: 13 }}
        >
          {busy ? "Running…" : "Run now"}
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          startIcon={<Iconify icon="mdi:cog-outline" width={16} />}
          onClick={onEdit}
          disabled={!canRun}
          sx={{ fontSize: 13 }}
        >
          Edit
        </Button>
        <IconButton
          size="small"
          aria-label="More report actions"
          onClick={(event) => setAnchor(event.currentTarget)}
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
        >
          <Iconify icon="mdi:dots-horizontal" width={18} />
        </IconButton>
        <Menu
          anchorEl={anchor}
          open={Boolean(anchor)}
          onClose={() => setAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
        >
          <MenuItem
            disabled={!config.has_taxonomy || busy || !canRun}
            onClick={() => {
              setAnchor(null);
              onRediscover();
            }}
            sx={{ maxWidth: 340, whiteSpace: "normal" }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:shape-plus-outline" width={18} />
            </ListItemIcon>
            <ListItemText
              primary="Rediscover categories"
              secondary="Runs reuse this report's categories so their shares compare. Rediscover to let the next run find new ones."
              secondaryTypographyProps={{ variant: "caption" }}
            />
          </MenuItem>
          <MenuItem
            disabled={!canRun}
            onClick={() => {
              setAnchor(null);
              onDelete();
            }}
            sx={{ color: "error.main" }}
          >
            <ListItemIcon sx={{ color: "inherit" }}>
              <Iconify icon="mdi:trash-can-outline" width={18} />
            </ListItemIcon>
            <ListItemText>Delete report</ListItemText>
          </MenuItem>
        </Menu>
      </Stack>
    </Stack>
  );
}

ReportHeader.propTypes = {
  config: PropTypes.object.isRequired,
  run: PropTypes.object,
  busy: PropTypes.bool,
  onRunNow: PropTypes.func.isRequired,
  onEdit: PropTypes.func.isRequired,
  onRediscover: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
  onDetails: PropTypes.func.isRequired,
};

function ReportSkeleton() {
  return (
    <Box sx={{ px: 3, pt: 2.5, maxWidth: CONTENT_WIDTH, mx: "auto" }}>
      <Skeleton variant="text" width={320} height={32} />
      <Skeleton variant="text" width={220} />
      <Skeleton variant="rounded" height={248} sx={{ mt: 2 }} />
      <Skeleton variant="text" width="60%" height={36} sx={{ mt: 3 }} />
      <Stack spacing={1.5} sx={{ mt: 1.5 }}>
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} variant="rounded" height={64} />
        ))}
      </Stack>
    </Box>
  );
}

/** A completed run: question, findings, changes, highlights, categories. */
function ReportBody({ config, run, lens, methodOpen, onMethod }) {
  const result = run.result || {};
  const meta = reportMetaParts(run, lens);
  const categories = result.categories || [];
  return (
    <Stack spacing={3.5}>
      <Box>
        <Typography
          variant="h4"
          component="h2"
          sx={{ fontSize: 22, lineHeight: "30px", fontWeight: 600 }}
        >
          {run.settings?.question || config.question}
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mt: 0.5, fontSize: 13, fontVariantNumeric: "tabular-nums" }}
        >
          {meta.join(" · ")}
        </Typography>
        <Box sx={{ mt: 1.5 }}>
          {categories.length ? (
            <Findings summary={result.summary} />
          ) : (
            <Box
              sx={{
                p: 2.5,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Typography variant="subtitle2">
                No categories in this run
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ fontSize: 13 }}
              >
                None of the {formatCount(run.sample_size)} sampled matched the
                question. Try a wider window, a different lens, or remove an
                attribute that filters.
              </Typography>
            </Box>
          )}
        </Box>
      </Box>
      {categories.length > 0 && (
        <>
          <ChangeCards changes={result.changes} />
          <Highlights highlights={result.summary?.highlights} />
          <CategoryTable
            reportId={config.id}
            run={run}
            attributes={run.settings?.attributes || config.attributes}
          />
        </>
      )}
      <MethodNote
        run={run}
        config={config}
        open={methodOpen}
        onToggle={() => onMethod((value) => !value)}
      />
    </Stack>
  );
}

ReportBody.propTypes = {
  config: PropTypes.object.isRequired,
  run: PropTypes.object.isRequired,
  lens: PropTypes.string,
  methodOpen: PropTypes.bool.isRequired,
  onMethod: PropTypes.func.isRequired,
};

// A failed run, and what to do next: run it again when another try could
// work, the License settings when the licence is why, nothing to click when
// only the model settings can fix it (its reason says what to change).
function FailedRun({ run, onRerun }) {
  const router = useRouter();
  const { isCloud } = useDeploymentMode();
  const canRun = useCanStartRuns();
  const next = failedRunNext(run, { cloud: isCloud });
  let action = null;
  if (next.rerun)
    action = (
      <Button color="inherit" size="small" onClick={onRerun} disabled={!canRun}>
        {next.rerun}
      </Button>
    );
  else if (next.license)
    action = (
      <Button
        color="inherit"
        size="small"
        sx={{ whiteSpace: "nowrap" }}
        onClick={() => router.push(paths.dashboard.settings.eeLicenses)}
      >
        License settings
      </Button>
    );
  return (
    <Alert severity={next.stopped ? "info" : "error"} action={action}>
      <Typography variant="subtitle2">
        {next.stopped
          ? "Stopped"
          : run.analysed_count
            ? "This run failed"
            : "Failed before analysing"}
      </Typography>
      <Typography variant="body2">{runOutcome(run)}</Typography>
      {runFailureReason(run)}
    </Alert>
  );
}

FailedRun.propTypes = {
  run: PropTypes.object.isRequired,
  onRerun: PropTypes.func.isRequired,
};

export default function ReportPage({
  projectId,
  configId,
  runId,
  categoryId,
  exampleId,
  onSelectRun,
  onView,
  onDeleted,
  onStarted,
  projectPending = false,
}) {
  const queryClient = useQueryClient();
  const canRun = useCanStartRuns();
  const configQuery = useInsightsConfig(configId);
  const config = configQuery.data;
  const effectiveRunId = runId || config?.latest_run?.id;
  const runQuery = useInsightsRun(effectiveRunId);
  const run = runQuery.data;
  const model = useReportModel(
    config,
    run?.status === "completed" ? run : null,
  );
  const [rerun, setRerun] = useState(null); // {rediscover}
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [methodOpen, setMethodOpen] = useState(false);
  const deleting = useCancellable("The report could not be deleted.");

  const openCategory = useCallback(
    (id) => onView({ category: id, example: null }, { push: true }),
    [onView],
  );
  const openExample = useCallback(
    (unitId) => onView({ example: unitId }),
    [onView],
  );
  const context = useMemo(
    () => ({
      ...model,
      projectId,
      runId: run?.id,
      unit: run?.unit || config?.unit,
      openCategory,
      openExample,
    }),
    [
      model,
      projectId,
      run?.id,
      run?.unit,
      config?.unit,
      openCategory,
      openExample,
    ],
  );

  // A new place (another run, a category, back to the report) starts at the top.
  useEffect(() => {
    document.getElementById("insights-pane")?.scrollTo?.({ top: 0 });
  }, [effectiveRunId, categoryId]);

  // A report not found while the project's reports are still being read may
  // be a deleted project's: the page says that once it knows.
  if (
    configQuery.isLoading ||
    (projectPending && configQuery.isError && projectMissing(configQuery.error))
  )
    return <ReportSkeleton />;
  if (configQuery.isError || !config)
    return (
      <Box sx={{ px: 3, pt: 2.5 }}>
        <ErrorState
          message={insightsErrorMessage(
            configQuery.error,
            "This report could not be loaded. It may have been deleted.",
          )}
          onRetry={() => configQuery.refetch()}
        />
      </Box>
    );

  const lens = run?.settings?.lens || config.lens;
  const active = isRunActive(run?.status);
  const latest = config.latest_run;
  const latestActive = isRunActive(latest?.status);
  const newerRunning = latest && latest.id !== effectiveRunId && latestActive;
  const completed = run?.status === "completed";
  const runs = config.runs || (latest ? [latest] : []);
  const sharedTaxonomy = runs
    .filter((r) => r.status === "completed")
    .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
    .slice(1)
    .every((r) => r.taxonomy_mode === "classify");
  const currentSegments = completed
    ? buildSegments(resultTops(model.categories), {
        order: model.order,
        mode: model.mode,
      })
    : [];
  const showExplorer = Boolean(categoryId) && completed;

  const remove = () =>
    deleting.run(async () => {
      await deleteConfig(config.id);
      forgetConfig(queryClient, config.id);
      setConfirmDelete(false);
      onDeleted();
      return true;
    });
  const showDetails = () => {
    setMethodOpen(true);
    setTimeout(
      () =>
        document
          .getElementById("insights-method")
          ?.scrollIntoView?.({ behavior: "smooth", block: "start" }),
      60,
    );
  };

  let body;
  if (!effectiveRunId)
    body = (
      <Alert
        severity="info"
        action={
          <Button
            color="inherit"
            size="small"
            disabled={!canRun}
            onClick={() => setRerun({ rediscover: false })}
          >
            Run it
          </Button>
        }
      >
        This report has not run yet.
      </Alert>
    );
  else if (runQuery.isLoading)
    body = (
      <Stack spacing={1.5}>
        <Skeleton variant="text" width="55%" height={36} />
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} variant="rounded" height={64} />
        ))}
      </Stack>
    );
  else if (runQuery.isError || !run)
    body = (
      <ErrorState
        message={insightsErrorMessage(
          runQuery.error,
          "This run could not be loaded.",
        )}
        onRetry={() => runQuery.refetch()}
      />
    );
  else if (active) body = <RunProgress run={run} />;
  else if (run.status === "failed")
    body = (
      <FailedRun run={run} onRerun={() => setRerun({ rediscover: false })} />
    );
  else
    body = (
      <ReportBody
        config={config}
        run={run}
        lens={lens}
        methodOpen={methodOpen}
        onMethod={setMethodOpen}
      />
    );

  return (
    <ReportProvider value={context}>
      <Box sx={{ minHeight: "100%", display: "flex", flexDirection: "column" }}>
        {showExplorer ? (
          <CategoryExplorer
            key={categoryId}
            categoryId={categoryId}
            reportName={config.name}
            run={run}
            lens={lens}
            // A history entry of its own, as opening the category was: a
            // replace left [report, report] and the browser's Back went
            // nowhere.
            onBack={() =>
              onView({ category: null, example: null }, { push: true })
            }
          />
        ) : (
          <>
            <Box
              sx={{
                flex: 1,
                width: "100%",
                maxWidth: CONTENT_WIDTH,
                mx: "auto",
                px: 3,
                pt: 2.5,
                pb: 4,
              }}
            >
              <ReportHeader
                config={config}
                run={run}
                busy={latestActive}
                onRunNow={() => setRerun({ rediscover: false })}
                onEdit={() => setEditing(true)}
                onRediscover={() => setRerun({ rediscover: true })}
                onDelete={() => setConfirmDelete(true)}
                onDetails={showDetails}
              />
              {newerRunning && (
                <Alert
                  severity="info"
                  sx={{ mt: 2 }}
                  action={
                    <Button
                      color="inherit"
                      size="small"
                      onClick={() => onSelectRun(latest.id)}
                    >
                      View progress
                    </Button>
                  }
                >
                  A newer run of this report is in progress.
                </Alert>
              )}
              <Box sx={{ mt: 2 }}>
                <TrendCard
                  runs={runs}
                  runId={effectiveRunId}
                  order={model.order}
                  currentSegments={currentSegments}
                  sharedTaxonomy={sharedTaxonomy}
                  onSelectRun={onSelectRun}
                  onCategory={completed ? openCategory : undefined}
                  onRunNow={
                    latestActive || !canRun
                      ? undefined
                      : () => setRerun({ rediscover: false })
                  }
                />
              </Box>
              <Box sx={{ mt: 3 }}>{body}</Box>
            </Box>
            {completed && model.categories.length > 0 && (
              <AskPanel key={run.id} run={run} lens={lens} />
            )}
          </>
        )}
      </Box>

      {completed && (
        <ExamplePanel
          open={Boolean(exampleId)}
          unitId={exampleId}
          onClose={() => onView({ example: null })}
        />
      )}
      {editing && (
        <EditDrawer
          open={editing}
          projectId={projectId}
          config={config}
          scopeLabel={run?.scope?.label || latest?.scope?.label}
          onClose={() => setEditing(false)}
          onRunNow={() => {
            setEditing(false);
            setRerun({ rediscover: false });
          }}
        />
      )}
      <RerunDialog
        open={Boolean(rerun)}
        projectId={projectId}
        config={config}
        initialRediscover={rerun?.rediscover}
        onClose={() => setRerun(null)}
        onStarted={(cid, rid) => {
          setRerun(null);
          onStarted(cid, rid);
        }}
      />
      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete this report?"
        content={
          <Stack spacing={1}>
            <Typography variant="body2">
              “{config.name}” and all {formatCount(runs.length)} of its runs
              will be deleted. This can&apos;t be undone.
            </Typography>
            {deleting.state.status === "error" && (
              <Alert severity="error">{deleting.state.error}</Alert>
            )}
          </Stack>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={remove}
            disabled={deleting.state.status === "busy"}
          >
            Delete
          </Button>
        }
      />
    </ReportProvider>
  );
}

ReportPage.propTypes = {
  projectId: PropTypes.string.isRequired,
  configId: PropTypes.string.isRequired,
  runId: PropTypes.string,
  categoryId: PropTypes.string,
  exampleId: PropTypes.string,
  onSelectRun: PropTypes.func.isRequired,
  onView: PropTypes.func.isRequired,
  onDeleted: PropTypes.func.isRequired,
  onStarted: PropTypes.func.isRequired,
  projectPending: PropTypes.bool,
};
