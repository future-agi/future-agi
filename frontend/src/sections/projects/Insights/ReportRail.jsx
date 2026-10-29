import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  IconButton,
  LinearProgress,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { useQueryClient } from "@tanstack/react-query";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import Iconify from "src/components/iconify";
import { unitWord } from "src/sections/projects/observePopulation";
import { useCanStartRuns } from "src/sections/projects/runPermission";
import {
  deleteConfig,
  failedRunNext,
  forgetConfig,
  insightsErrorMessage,
  insightsKeys,
  patchConfig,
  projectMissing,
  runFailureReason,
} from "./insightsApi";
import {
  buildColorOrder,
  buildSegments,
  runTops,
} from "./insightsDistribution";
import {
  formatCount,
  formatPercent,
  relativeTime,
  runOutcome,
  runReadAny,
} from "./insightsFormat";
import { useCancellable } from "./insightsHooks";
import {
  STAGES,
  TEMPLATES,
  isRunActive,
  lensFor,
  stageIndex,
  unitTitle,
} from "./insightsLenses";
import { ErrorState, StackedBar, Swatch } from "./parts";

export const RAIL_WIDTH = 340;
const LEGEND_ITEMS = 6;

const lensLabel = (key) => {
  const lens = lensFor(key);
  return lens.key === "custom" ? "Custom" : lens.title;
};

function CardLegend({ segments }) {
  return (
    <Box
      component="ul"
      sx={{
        m: 0,
        p: 0,
        listStyle: "none",
        display: "flex",
        flexWrap: "wrap",
        columnGap: 1.5,
        rowGap: 0.25,
      }}
    >
      {segments.slice(0, LEGEND_ITEMS).map((segment) => (
        <Stack
          key={segment.id}
          component="li"
          direction="row"
          spacing={0.5}
          alignItems="center"
          sx={{ minWidth: 0, maxWidth: "100%" }}
        >
          <Swatch color={segment.color} size={8} />
          <Typography
            variant="caption"
            noWrap
            sx={{ minWidth: 0, maxWidth: 170 }}
            title={segment.name}
          >
            {segment.name}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
          >
            {formatPercent(segment.share)}
          </Typography>
        </Stack>
      ))}
    </Box>
  );
}

CardLegend.propTypes = { segments: PropTypes.array.isRequired };

function CardMenu({ config, onRerun, onRename, onDelete }) {
  const [anchor, setAnchor] = useState(null);
  const canRun = useCanStartRuns();
  const close = () => setAnchor(null);
  const active = isRunActive(config.latest_run?.status);
  return (
    <>
      <IconButton
        size="small"
        aria-label={`Actions for ${config.name}`}
        onClick={(event) => {
          event.stopPropagation();
          setAnchor(event.currentTarget);
        }}
        sx={{ mt: -0.5, mr: -0.75, color: "text.secondary" }}
      >
        <Iconify icon="mdi:dots-horizontal" width={18} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        onClick={(event) => event.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <MenuItem
          disabled={active || !canRun}
          onClick={() => {
            close();
            onRerun(config);
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:play-outline" width={18} />
          </ListItemIcon>
          <ListItemText>{active ? "Running…" : "Run now"}</ListItemText>
        </MenuItem>
        <MenuItem
          disabled={!canRun}
          onClick={() => {
            close();
            onRename();
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:pencil-outline" width={18} />
          </ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>
        <MenuItem
          disabled={!canRun}
          onClick={() => {
            close();
            onDelete(config);
          }}
          sx={{ color: "error.main" }}
        >
          <ListItemIcon sx={{ color: "inherit" }}>
            <Iconify icon="mdi:trash-can-outline" width={18} />
          </ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>
    </>
  );
}

CardMenu.propTypes = {
  config: PropTypes.object.isRequired,
  onRerun: PropTypes.func.isRequired,
  onRename: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
};

function RenameField({ config, onDone }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(config.name);
  const cancelled = useRef(false);
  const saving = useCancellable("The report could not be renamed.");
  const save = () =>
    saving.run(async () => {
      const name = value.trim();
      if (cancelled.current || !name || name === config.name) {
        onDone();
        return null;
      }
      const updated = await patchConfig(config.id, {
        name: name.slice(0, 255),
      });
      queryClient.setQueryData(insightsKeys.config(config.id), (current) =>
        current ? { ...current, name: updated?.name || name } : current,
      );
      queryClient.invalidateQueries({ queryKey: ["insights", "configs"] });
      onDone();
      return updated;
    });
  return (
    <TextField
      size="small"
      autoFocus
      fullWidth
      value={value}
      onClick={(event) => event.stopPropagation()}
      onChange={(event) => setValue(event.target.value)}
      onBlur={save}
      onKeyDown={(event) => {
        event.stopPropagation();
        // Enter commits through blur, so the name is saved once.
        if (event.key === "Enter")
          event.currentTarget.querySelector("input")?.blur();
        if (event.key === "Escape") {
          cancelled.current = true;
          onDone();
        }
      }}
      inputProps={{ "aria-label": "Report name", maxLength: 255 }}
      disabled={saving.state.status === "busy"}
      error={saving.state.status === "error"}
      helperText={saving.state.status === "error" ? saving.state.error : ""}
      sx={{
        "& .MuiInputBase-input": { py: 0.5, fontSize: 14, fontWeight: 600 },
      }}
    />
  );
}

RenameField.propTypes = {
  config: PropTypes.object.isRequired,
  onDone: PropTypes.func.isRequired,
};

function ReportCard({ config, selected, onOpen, onRerun, onDelete }) {
  const theme = useTheme();
  const mode = theme.palette.mode;
  const [renaming, setRenaming] = useState(false);
  const run = config.latest_run;
  const active = isRunActive(run?.status);
  const segments = useMemo(() => {
    if (run?.status !== "completed") return [];
    const order = buildColorOrder({ taxonomy: config.taxonomy, runs: [run] });
    return buildSegments(runTops(run), { order, mode, max: LEGEND_ITEMS });
  }, [config.taxonomy, run, mode]);

  const when = run
    ? active
      ? "Running"
      : `Last run ${relativeTime(run.finished_at || run.created_at)}`
    : "Not run yet";
  const stage = active ? STAGES[stageIndex(run.stage)] : null;
  const failed = failedRunNext(run);
  const canRun = useCanStartRuns();
  const open = () => !renaming && onOpen(config.id);

  return (
    <Box
      role="link"
      tabIndex={0}
      aria-current={selected ? "page" : undefined}
      aria-label={`Open report ${config.name}`}
      onClick={open}
      onKeyDown={(event) => {
        if (event.key === "Enter" && event.target === event.currentTarget)
          open();
      }}
      sx={{
        position: "relative",
        p: 1.75,
        pr: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: selected ? "primary.main" : "divider",
        boxShadow: selected
          ? `0 0 0 1px ${theme.palette.primary.main}`
          : "none",
        bgcolor: "background.paper",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: 1,
        transition: "border-color 120ms",
        "&:hover": selected ? undefined : { borderColor: "text.disabled" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: 2,
        },
      }}
    >
      <Stack direction="row" spacing={0.5} alignItems="flex-start">
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {renaming ? (
            <RenameField config={config} onDone={() => setRenaming(false)} />
          ) : (
            <Typography
              variant="subtitle2"
              title={config.name}
              sx={{
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
                lineHeight: 1.4,
              }}
            >
              {config.name || config.question}
            </Typography>
          )}
          <Typography
            variant="caption"
            color="text.secondary"
            noWrap
            component="div"
            sx={{ mt: 0.25 }}
          >
            {unitTitle(config.unit)} · {lensLabel(config.lens)} · {when}
          </Typography>
        </Box>
        <CardMenu
          config={config}
          onRerun={onRerun}
          onRename={() => setRenaming(true)}
          onDelete={onDelete}
        />
      </Stack>

      {active ? (
        <Stack spacing={0.75}>
          <Typography variant="caption" color="text.secondary">
            {run.status === "pending"
              ? "Queued"
              : `${stage?.label || "Working"}…`}{" "}
            {stage?.detail ? `· ${stage.detail}` : ""}
          </Typography>
          <LinearProgress
            variant="determinate"
            value={Math.max(
              6,
              (stageIndex(run.stage) / (STAGES.length - 1)) * 100,
            )}
            aria-label="Run progress"
            sx={{ height: 3, borderRadius: 2 }}
          />
        </Stack>
      ) : run?.status === "failed" ? (
        <Stack spacing={0.5}>
          <Typography
            variant="caption"
            color={failed.stopped ? "text.secondary" : "error.main"}
          >
            {failed.stopped
              ? "Last run stopped"
              : runReadAny(run)
                ? "Last run failed"
                : "Failed before analysing"}
          </Typography>
          <Typography variant="caption">
            {runOutcome(run, config.lens)}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {runFailureReason(run)}
          </Typography>
          {failed.rerun && (
            <Button
              size="small"
              color="inherit"
              sx={{ alignSelf: "flex-start" }}
              disabled={!canRun}
              onClick={(event) => {
                event.stopPropagation();
                onRerun(config);
              }}
            >
              {failed.rerun}
            </Button>
          )}
        </Stack>
      ) : segments.length > 0 ? (
        <Stack spacing={1}>
          <StackedBar segments={segments} height={8} />
          <CardLegend segments={segments} />
        </Stack>
      ) : null}

      {run?.status === "completed" && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {formatCount(run.analysed_count ?? run.sample_size)} of{" "}
          {formatCount(run.sample_size)}{" "}
          {unitWord(config.unit, run.sample_size)} analysed
        </Typography>
      )}
    </Box>
  );
}

ReportCard.propTypes = {
  config: PropTypes.object.isRequired,
  selected: PropTypes.bool,
  onOpen: PropTypes.func.isRequired,
  onRerun: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
};

function RailTemplates({ onNew }) {
  return (
    <Stack spacing={0.5}>
      {TEMPLATES.map((template) => (
        <Stack
          key={template.lens}
          direction="row"
          spacing={1.25}
          alignItems="center"
          component="button"
          type="button"
          onClick={() => onNew(template.lens)}
          sx={{
            px: 1,
            py: 0.75,
            mx: -1,
            border: 0,
            borderRadius: 1,
            bgcolor: "transparent",
            font: "inherit",
            color: "text.primary",
            textAlign: "left",
            cursor: "pointer",
            "&:hover": { bgcolor: "action.hover" },
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
            },
          }}
        >
          <Iconify
            icon={template.icon}
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography variant="body2" sx={{ fontSize: 13 }}>
            {template.title}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}

RailTemplates.propTypes = { onNew: PropTypes.func.isRequired };

/**
 * The list of reports beside the open one: a card per report with its latest
 * distribution, the templates, and a collapse toggle.
 */
export default function ReportRail({
  configsQuery,
  selectedId,
  collapsed,
  onToggle,
  onNew,
  onOpen,
  onRerun,
  onDeleted,
}) {
  const queryClient = useQueryClient();
  const configs = configsQuery.data || [];
  // The page says the project is gone; the list adds no second error.
  const projectGone =
    configsQuery.isError && projectMissing(configsQuery.error);
  const [toDelete, setToDelete] = useState(null);
  const deleting = useCancellable("The report could not be deleted.");

  const remove = () =>
    deleting.run(async () => {
      const id = toDelete.id;
      await deleteConfig(id);
      forgetConfig(queryClient, id);
      setToDelete(null);
      onDeleted(id);
      return true;
    });

  if (collapsed)
    return (
      <Stack
        component="nav"
        aria-label="Reports"
        alignItems="center"
        spacing={1}
        sx={{
          width: 48,
          flexShrink: 0,
          py: 1.5,
          borderRight: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Tooltip title="Show reports" placement="right" arrow>
          <IconButton size="small" aria-label="Show reports" onClick={onToggle}>
            <Iconify icon="mdi:chevron-double-right" width={18} />
          </IconButton>
        </Tooltip>
        <Tooltip title="New report" placement="right" arrow>
          <IconButton
            size="small"
            aria-label="New report"
            onClick={() => onNew()}
          >
            <Iconify icon="mdi:plus" width={18} />
          </IconButton>
        </Tooltip>
      </Stack>
    );

  return (
    <Box
      component="nav"
      aria-label="Reports"
      sx={{
        width: RAIL_WIDTH,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        borderRight: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Box sx={{ px: 2, pt: 2, pb: 1.5 }}>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Typography
            variant="m3"
            component="h1"
            fontWeight={600}
            sx={{ flex: 1 }}
          >
            Insights
          </Typography>
          <Button
            size="small"
            variant="contained"
            startIcon={<Iconify icon="mdi:plus" width={16} />}
            onClick={() => onNew()}
          >
            New report
          </Button>
          <Tooltip title="Hide reports" arrow>
            <IconButton
              size="small"
              aria-label="Hide reports"
              onClick={onToggle}
              sx={{ color: "text.secondary" }}
            >
              <Iconify icon="mdi:chevron-double-left" width={18} />
            </IconButton>
          </Tooltip>
        </Stack>
        <Typography
          variant="caption"
          color="text.secondary"
          component="p"
          sx={{ mt: 0.5, mb: 0, pr: 4 }}
        >
          Find what users do and where the agent fails, with numbers you can
          trust.
        </Typography>
      </Box>

      <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", px: 2, pb: 2 }}>
        {configsQuery.isLoading ? (
          <Stack spacing={1.25}>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} variant="rounded" height={132} />
            ))}
          </Stack>
        ) : projectGone ? null : configsQuery.isError ? (
          <ErrorState
            message={insightsErrorMessage(
              configsQuery.error,
              "Reports could not be loaded.",
            )}
            onRetry={() => configsQuery.refetch()}
          />
        ) : (
          <Stack spacing={1.25}>
            {configs.map((config) => (
              <ReportCard
                key={config.id}
                config={config}
                selected={config.id === selectedId}
                onOpen={onOpen}
                onRerun={onRerun}
                onDelete={setToDelete}
              />
            ))}
            <Box sx={{ pt: configs.length ? 1.5 : 0 }}>
              <Typography
                variant="overline"
                color="text.secondary"
                component="h2"
                sx={{ display: "block", mb: 0.5 }}
              >
                Start from a template
              </Typography>
              <RailTemplates onNew={onNew} />
            </Box>
          </Stack>
        )}
      </Box>

      <ConfirmDialog
        open={Boolean(toDelete)}
        onClose={() => {
          setToDelete(null);
          deleting.reset();
        }}
        title="Delete this report?"
        content={
          <Stack spacing={1}>
            <Typography variant="body2">
              “{toDelete?.name}” and all of its runs will be deleted. This
              can&apos;t be undone.
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
    </Box>
  );
}

ReportRail.propTypes = {
  configsQuery: PropTypes.object.isRequired,
  selectedId: PropTypes.string,
  collapsed: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  onNew: PropTypes.func.isRequired,
  onOpen: PropTypes.func.isRequired,
  onRerun: PropTypes.func.isRequired,
  onDeleted: PropTypes.func.isRequired,
};
