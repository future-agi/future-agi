// the interface contract (lane w3-rows-in A7, D11): the dataset header's `Sources n` chip.
// Each saved import source reads `support-prod · Resolution = Fail · last
// 24 h · last refresh 2 min ago: +4 rows (pending)` with Refresh now ·
// Pause · Edit · Remove. Refresh adds only traces / sessions the source has
// not seen: as pending changes, or as a saved version (the source's mode).
// Renders nothing when the dataset has no source.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  Link,
  MenuItem,
  Popover,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import CustomTooltip from "src/components/tooltip";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { relativeTime } from "src/utils/format-time";
import { useGetDatasetColumns } from "src/api/develop/develop-detail";
import { plural } from "src/sections/common/vocab";
import { notify } from "src/sections/common/ux/toast";
import { useChangeSetStore } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { revealNewRows } from "src/sections/develop-detail/DataTab/DataGrid/newRowsReveal";
import { IMPORT_WINDOW_OPTIONS, toChangeSetOps } from "./mappingModel";
import {
  useDeleteImportSource,
  useImportSources,
  useRefreshImportSource,
  useUpdateImportSource,
} from "./importSourcesApi";

const clock = (iso) =>
  new Date(iso).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

/** `last refresh 2 minutes ago: +4 rows (pending)`. */
function lastRefreshText(src) {
  const lr = src?.last_refresh;
  if (!lr?.at) return null;
  const when = relativeTime(lr.at);
  // The traces / sessions the source matched when it was saved count as
  // seen (the import that saved it took them, or they were left out).
  if (lr.mode === "saved")
    return `saved ${when}${lr.added ? ` · the ${plural(lr.added, src.kind === "sessions" ? "session" : "trace")} it matches now ${lr.added === 1 ? "counts" : "count"} as seen` : ""}`;
  if (!lr.added) return `last refresh ${when}: nothing new`;
  const where =
    lr.mode === "version" && lr.version ? `saved v${lr.version}` : "pending";
  return `last refresh ${when}: +${plural(lr.added, "row")} (${where})`;
}

/** `support-prod · Resolution = Fail · last 24 h`. */
const sourceScopeText = (src) =>
  [src.project_name, src.filter_text ?? `all ${src.kind}`, src.window_text]
    .filter(Boolean)
    .join(" · ");

const traceLabel = (id) => `Trace ${id.slice(0, 4)}…${id.slice(-4)}`;

function EditSourceDialog({ source, onClose, datasetId }) {
  const update = useUpdateImportSource(datasetId);
  const [name, setName] = useState(source.name);
  const [window, setWindow] = useState(source.window);
  const [mode, setMode] = useState(source.on_refresh);
  const save = () =>
    update.mutate(
      { sourceId: source.id, name: name.trim(), window, on_refresh: mode },
      {
        onSuccess: () => {
          notify(`Saved "${name.trim()}"`, { key: "import-source" });
          onClose();
        },
        onError: (e) =>
          notify(getRequestErrorMessage(e, "Couldn't save the source."), {
            key: "import-source",
            variant: "error",
          }),
      },
    );
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Edit source</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField
            size="small"
            label="Source name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={!name.trim()}
            helperText={!name.trim() ? "Name the source" : undefined}
            inputProps={{ "data-testid": "source-edit-name" }}
          />
          <TextField
            select
            size="small"
            label={`Window: ${source.kind} from the`}
            value={window}
            onChange={(e) => setWindow(e.target.value)}
          >
            {IMPORT_WINDOW_OPTIONS.map((w) => (
              <MenuItem key={w.id} value={w.id}>
                {w.label.replace(/^Last /, "last ")}
              </MenuItem>
            ))}
          </TextField>
          <Box>
            <Typography typography="s2" color="text.secondary">
              Each refresh
            </Typography>
            <RadioGroup value={mode} onChange={(e) => setMode(e.target.value)}>
              <FormControlLabel
                value="pending"
                control={<Radio size="small" />}
                label={
                  <Typography typography="s2">
                    adds new rows as pending changes
                  </Typography>
                }
              />
              <FormControlLabel
                value="version"
                control={<Radio size="small" />}
                label={
                  <Typography typography="s2">saves a new version</Typography>
                }
              />
            </RadioGroup>
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={!name.trim() || update.isPending}
          onClick={save}
          data-testid="source-edit-save"
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}
EditSourceDialog.propTypes = {
  source: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  datasetId: PropTypes.string.isRequired,
};

function SourceItem({ source, datasetId, columnIdByName, canWrite }) {
  const queryClient = useQueryClient();
  const refresh = useRefreshImportSource(datasetId);
  const update = useUpdateImportSource(datasetId);
  const remove = useDeleteImportSource(datasetId);
  const [editing, setEditing] = useState(false);
  const [openTrace, setOpenTrace] = useState(null);
  const traces = source.last_refresh?.trace_ids ?? [];

  const onRefresh = () =>
    refresh.mutate(source.id, {
      onSuccess: (res) => {
        if (!res?.added) {
          notify(
            `Nothing new since ${clock(res?.since ?? source.created_at)}`,
            {
              key: `import-source-${source.id}`,
            },
          );
          return;
        }
        if (res.version) {
          queryClient.invalidateQueries({ queryKey: ["develop"] });
          notify(
            `Refreshed "${source.name}": +${plural(res.added, "row")} · saved v${res.version}`,
            { key: `import-source-${source.id}`, variant: "success" },
          );
          return;
        }
        const ops = toChangeSetOps(res.operations ?? [], {
          columnIdByName,
          hint: `Refreshed "${source.name}": +${plural(res.added, "row")}`,
          stamp: `src-${Date.now().toString(36)}-`,
        });
        const cs = useChangeSetStore.getState();
        ops.forEach((op) => cs.addOp(datasetId, op));
        revealNewRows(
          datasetId,
          ops.map((o) => o.temp_id),
        );
        notify(`Refreshed "${source.name}": +${plural(res.added, "row")}`, {
          key: `import-source-${source.id}`,
          variant: "success",
        });
      },
      onError: (e) =>
        notify(getRequestErrorMessage(e, "Couldn't refresh the source."), {
          key: `import-source-${source.id}`,
          variant: "error",
        }),
    });

  return (
    <Stack spacing={0.75} data-testid="import-source">
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography typography="s1" fontWeight={600} noWrap>
          {source.name}
        </Typography>
        {source.paused ? (
          <Chip size="small" label="Paused" sx={{ height: 20 }} />
        ) : null}
      </Stack>
      <Typography
        typography="s2"
        color="text.secondary"
        data-testid="import-source-line"
      >
        {[sourceScopeText(source), lastRefreshText(source)]
          .filter(Boolean)
          .join(" · ")}
      </Typography>
      {traces.length ? (
        <Stack direction="row" gap={0.75} flexWrap="wrap">
          {traces.map((id) => (
            <CustomTooltip key={id} show title={id} size="small" arrow>
              <Link
                component="button"
                type="button"
                typography="s3"
                sx={{ fontFamily: "monospace" }}
                onClick={() => setOpenTrace(id)}
                data-testid="import-source-trace"
              >
                {traceLabel(id)}
              </Link>
            </CustomTooltip>
          ))}
        </Stack>
      ) : null}
      {canWrite ? (
        <Stack direction="row" gap={0.5} flexWrap="wrap">
          <CustomTooltip
            show={source.paused}
            title="Paused: resume it to refresh"
            size="small"
          >
            <span>
              <Button
                size="small"
                variant="outlined"
                startIcon={<Iconify icon="mdi:refresh" width={16} />}
                disabled={source.paused || refresh.isPending}
                onClick={onRefresh}
                data-testid="import-source-refresh"
              >
                Refresh now
              </Button>
            </span>
          </CustomTooltip>
          <Button
            size="small"
            color="inherit"
            onClick={() =>
              update.mutate({ sourceId: source.id, paused: !source.paused })
            }
            data-testid="import-source-pause"
          >
            {source.paused ? "Resume" : "Pause"}
          </Button>
          <Button
            size="small"
            color="inherit"
            onClick={() => setEditing(true)}
            data-testid="import-source-edit"
          >
            Edit
          </Button>
          <Button
            size="small"
            color="error"
            onClick={() =>
              remove.mutate(source.id, {
                onSuccess: () =>
                  notify(`Removed "${source.name}"`, {
                    key: `import-source-${source.id}`,
                  }),
              })
            }
            data-testid="import-source-remove"
          >
            Remove
          </Button>
        </Stack>
      ) : null}
      {openTrace ? (
        <TraceDetailDrawerV2
          traceId={openTrace}
          projectId={source.project_id}
          open
          onClose={() => setOpenTrace(null)}
        />
      ) : null}
      {editing ? (
        <EditSourceDialog
          source={source}
          datasetId={datasetId}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </Stack>
  );
}
SourceItem.propTypes = {
  source: PropTypes.object.isRequired,
  datasetId: PropTypes.string.isRequired,
  columnIdByName: PropTypes.object.isRequired,
  canWrite: PropTypes.bool,
};

export default function SavedSourcesChip({ datasetId, canWrite = true }) {
  const { data: sources = [] } = useImportSources(datasetId);
  const { data: columns } = useGetDatasetColumns(datasetId, {
    enabled: Boolean(datasetId) && sources.length > 0,
  });
  const [anchor, setAnchor] = useState(null);
  if (!sources.length) return null;
  const columnIdByName = Object.fromEntries(
    (columns ?? []).map((c) => [c.name, c.id]),
  );
  return (
    <>
      <Chip
        size="small"
        variant="outlined"
        icon={<Iconify icon="mdi:database-import-outline" width={16} />}
        label={`Sources ${sources.length}`}
        onClick={(e) => setAnchor(e.currentTarget)}
        data-testid="import-sources-chip"
        sx={{ typography: "s2" }}
      />
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 460, p: 2 } } }}
      >
        <Stack spacing={1.5} divider={<Divider flexItem />}>
          {sources.map((s) => (
            <SourceItem
              key={s.id}
              source={s}
              datasetId={datasetId}
              columnIdByName={columnIdByName}
              canWrite={canWrite}
            />
          ))}
        </Stack>
      </Popover>
    </>
  );
}
SavedSourcesChip.propTypes = {
  datasetId: PropTypes.string,
  canWrite: PropTypes.bool,
};
