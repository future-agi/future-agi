import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Skeleton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { KeyHintTitle } from "src/sections/common/ux/KeyHintTooltip";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";
import { localiseCap } from "src/utils/keymap";
import TextDiff, {
  TextDiffLegend,
} from "src/sections/common/ValueViewer/TextDiff";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import {
  apiErrorMessage,
  latestOf,
  useDatasetVersions,
  useSaveDatasetVersion,
  useVersionRows,
} from "src/api/develop/dataset-versions";
import { useDataGridView } from "../DataGrid/useDataGridView";
import { useAuthContext } from "src/auth/hooks";
import { ROW_STATUS_LABEL } from "./changeSetStore";
import {
  createdColumnsText,
  useChangeSet,
  useCreatedColumnsStore,
} from "./changeSetStore";
import {
  ChangeItem,
  ChangeList,
  KindBadge,
  SplitDelta,
  sectionLabelSx,
} from "./ChangeParts";
import {
  allTags,
  describeChangeSet,
  groupOpsByRow,
  pendingCountsText,
  shortValue,
  tagError,
  tagMoveText,
} from "./versionText";

const cellText = (cell) => cell?.cell_value ?? cell?.cellValue ?? null;

/**
 * Save version (PRODUCT-SPEC §3.5): the pending change-set grouped by row,
 * each change with Revert and a word diff, a description (the auto text is
 * saved when left empty) and an optional tag. ⌘↵ saves.
 */
const NO_COLS = [];

const SaveVersionDialog = ({ open, datasetId, onClose, onSaved }) => {
  const cs = useChangeSet(datasetId);
  // integration (DV-01): columns added with these changes (not versioned).
  const createdCols = useCreatedColumnsStore(
    (st) => st.cols[datasetId] ?? NO_COLS,
  );
  const { user } = useAuthContext();
  const { data: versions = [], refetch: refetchVersions } =
    useDatasetVersions(datasetId);
  const latest = latestOf(versions);
  const baseId = cs.baseVersionId ?? latest?.id ?? null;
  const base = versions.find((v) => v.id === baseId) ?? latest;
  const nextNumber = (latest?.number ?? 0) + 1;

  const [description, setDescription] = useState("");
  const [tag, setTag] = useState("");
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState(null);
  const [violations, setViolations] = useState(null);
  const [rebasedOn, setRebasedOn] = useState(null);
  const gridView = useDataGridView(datasetId);
  const save = useSaveDatasetVersion(datasetId);

  useEffect(() => {
    if (!open) return;
    setDescription("");
    setTag("");
    setConflict(false);
    setError(null);
    setViolations(null);
    setRebasedOn(null);
  }, [open]);

  const { rows: groups, selectionDelete } = useMemo(
    () => groupOpsByRow(cs.ops),
    [cs.ops],
  );
  const realRowIds = useMemo(
    () => groups.filter((g) => !g.isNew).map((g) => g.rowId),
    [groups],
  );
  // New rows with cells need the column names too.
  const newRowsHaveCells = groups.some(
    (g) =>
      g.isNew && g.items.some((i) => Object.keys(i.cells ?? {}).length > 0),
  );
  const lookup = useVersionRows(datasetId, latest?.id, realRowIds, {
    enabled:
      open &&
      Boolean(latest?.id) &&
      (realRowIds.length > 0 || newRowsHaveCells),
  });
  const rowsById = useMemo(
    () => new Map((lookup.data?.rows ?? []).map((r) => [r.row_id, r])),
    [lookup.data],
  );
  const columnsById = useMemo(
    () => new Map((lookup.data?.columns ?? []).map((c) => [c.id, c])),
    [lookup.data],
  );
  const splitsById = useMemo(
    () => new Map((lookup.data?.splits ?? []).map((s) => [s.id, s])),
    [lookup.data],
  );

  const newRowNumber = useCallback(
    (tempId) => {
      const i = cs.newRowIds.indexOf(tempId);
      return i >= 0 ? i + 1 : null;
    },
    [cs.newRowIds],
  );
  const rowLabel = useCallback(
    (rowId) => {
      const row = rowsById.get(rowId);
      if (row) return `Row ${row.order + 1}`;
      const n = newRowNumber(rowId);
      return n ? `New row ${n}` : "Row";
    },
    [rowsById, newRowNumber],
  );
  const columnName = useCallback(
    (id) => columnsById.get(id)?.name ?? "column",
    [columnsById],
  );
  const splitOf = useCallback(
    (id) => splitsById.get(id) ?? { id, name: "split", color: "grey" },
    [splitsById],
  );

  const autoDescription = useMemo(
    () =>
      describeChangeSet(cs.ops, {
        rowLabel,
        columnName,
        splitName: (id) => splitOf(id).name,
        actorName: user?.name ?? user?.full_name ?? null,
      }),
    [cs.ops, rowLabel, columnName, splitOf, user],
  );

  const tagValue = tag.trim();
  const tagProblem = tagError(tagValue);
  const tagHelp = tagProblem ?? tagMoveText(tagValue, versions, nextNumber);

  const doSave = useCallback(async () => {
    if (!cs.hasPending || tagProblem || save.isPending) return;
    setError(null);
    try {
      const result = await save.mutateAsync({
        base_version_id: baseId,
        description: description.trim() || autoDescription,
        ...(tagValue ? { tag: tagValue } : {}),
        operations: cs.toOperations(),
      });
      setConflict(false);
      // integration: the columns added with these changes are kept (not pending).
      useCreatedColumnsStore.getState().clear(datasetId);
      onSaved?.(result?.version, result);
    } catch (e) {
      if (e?.isVersionConflict) setConflict(true);
      else if (e?.schemaViolations) setViolations(e.schemaViolations);
      else setError(apiErrorMessage(e, "Couldn't save the version."));
    }
  }, [
    cs,
    tagProblem,
    save,
    baseId,
    description,
    autoDescription,
    tagValue,
    onSaved,
    datasetId,
  ]);

  const reloadAndReapply = useCallback(async () => {
    const res = await refetchVersions();
    const fresh = latestOf(res.data ?? []);
    if (fresh) {
      cs.setBaseVersion(fresh.id);
      setRebasedOn(fresh.number);
    }
    setConflict(false);
  }, [refetchVersions, cs]);

  const onKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      doSave();
    }
  };

  const countsText = pendingCountsText(cs.counts);
  const hasCellDiffs = cs.ops.some((o) => o.op === "update_cell");

  return (
    <Dialog
      open={open}
      onClose={save.isPending ? undefined : onClose}
      maxWidth={false}
      onKeyDown={onKeyDown}
      PaperProps={{ sx: { width: 720, maxWidth: "calc(100vw - 32px)" } }}
    >
      <DialogTitle sx={{ p: 2, pb: 1 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Typography typography="m2" fontWeight="fontWeightBold">
            Save version
          </Typography>
          <VersionNumber number={nextNumber} />
          <Box sx={{ flex: 1 }} />
          <IconButton
            onClick={onClose}
            disabled={save.isPending}
            aria-label="Close"
          >
            <Iconify icon="mdi:close" />
          </IconButton>
        </Stack>
        <Typography typography="s2" color="text.secondary" sx={{ mt: 0.25 }}>
          {countsText || "No changes"}
          {rebasedOn
            ? ` · re-applied on top of v${rebasedOn}`
            : base
              ? ` · on top of v${base.number}`
              : ""}
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ px: 2, pb: 1 }}>
        {conflict && (
          <Alert
            severity="warning"
            sx={{ mb: 1.5 }}
            action={
              <Button color="inherit" size="small" onClick={reloadAndReapply}>
                Reload latest and re-apply
              </Button>
            }
          >
            Someone saved v{nextNumber} while you were editing. Your changes are
            kept.
          </Alert>
        )}
        {error && (
          <Alert severity="error" sx={{ mb: 1.5 }}>
            {error}
          </Alert>
        )}
        {violations?.length > 0 && (
          <Alert
            severity="error"
            sx={{ mb: 1.5 }}
            data-testid="save-schema-violations"
            action={
              <Stack direction="row" gap={0.5}>
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => {
                    gridView.setRowIds(
                      [
                        ...new Set(
                          violations.map((v) => v.row_id ?? v.temp_id),
                        ),
                      ],
                      "Schema",
                    );
                    onClose?.();
                  }}
                >
                  Show
                </Button>
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => {
                    violations.forEach((v) =>
                      v.row_id
                        ? cs.revert(v.row_id, v.column_id)
                        : cs.revert(v.temp_id),
                    );
                    setViolations(null);
                  }}
                >
                  Revert these
                </Button>
              </Stack>
            }
          >
            <Typography typography="s2" fontWeight={600}>
              {`${violations.length} ${violations.length === 1 ? "change doesn't" : "changes don't"} match the schema`}
            </Typography>
            {violations.slice(0, 6).map((v) => (
              <Typography
                key={`${v.row_id ?? v.temp_id}-${v.path}`}
                typography="s3"
                component="div"
              >
                {`${v.row_label}: ${v.message}`}
              </Typography>
            ))}
          </Alert>
        )}

        <Stack direction="row" alignItems="center" sx={{ mb: 0.75 }}>
          <Typography sx={sectionLabelSx}>Changes</Typography>
          <Box sx={{ flex: 1 }} />
          {hasCellDiffs && <TextDiffLegend />}
        </Stack>
        <ChangeList>
          {!cs.hasPending && (
            <Typography typography="s1" color="text.secondary" sx={{ p: 2 }}>
              Every change was reverted. Nothing to save.
            </Typography>
          )}
          {selectionDelete && (
            <Box sx={{ px: 1.5, py: 1 }}>
              <ChangeItem
                label="Rows"
                onRevert={() => cs.revert(selectionDelete.opId)}
              >
                All rows matching the current filter · deleted
              </ChangeItem>
            </Box>
          )}
          {groups.map((g) => (
            <Box key={g.rowId} sx={{ px: 1.5, py: 1 }}>
              <Stack direction="row" alignItems="center" gap={1}>
                {lookup.isLoading && !g.isNew ? (
                  <Skeleton width={64} height={20} />
                ) : (
                  <Typography typography="s1" fontWeight="fontWeightSemiBold">
                    {rowLabel(g.rowId)}
                  </Typography>
                )}
                {g.isNew && <KindBadge kind="new" />}
                <Box sx={{ flex: 1 }} />
                {g.items.length > 1 && (
                  <Button
                    size="small"
                    color="inherit"
                    onClick={() => cs.revert(g.rowId)}
                    sx={{ color: "text.secondary", minWidth: 0, px: 1 }}
                  >
                    Revert row
                  </Button>
                )}
              </Stack>
              {g.items.map((item) => {
                if (item.kind === "cell")
                  return (
                    <ChangeItem
                      key={item.opId}
                      label={columnName(item.columnId)}
                      onRevert={() => cs.revert(item.opId)}
                    >
                      <TextDiff
                        from={item.before ?? ""}
                        to={item.after ?? ""}
                      />
                    </ChangeItem>
                  );
                if (item.kind === "metadata")
                  return (
                    <ChangeItem
                      key={item.opId}
                      label="Row metadata"
                      onRevert={() => cs.revert(item.opId)}
                    >
                      <TextDiff
                        from={item.before ?? {}}
                        to={item.after ?? {}}
                        kind="json"
                      />
                    </ChangeItem>
                  );
                if (item.kind === "splits")
                  return (
                    <ChangeItem
                      key={`${item.opId}-${g.rowId}`}
                      label="Splits"
                      onRevert={() => cs.revert(item.opId)}
                    >
                      <SplitDelta
                        add={item.add}
                        remove={item.remove}
                        splitOf={splitOf}
                      />
                    </ChangeItem>
                  );
                if (item.kind === "added") {
                  const filled = Object.entries(item.cells).filter(
                    ([, v]) => v != null && v !== "",
                  );
                  return (
                    <ChangeItem
                      key={item.opId}
                      label="Added"
                      onRevert={() => cs.revert(item.opId)}
                      revertLabel="Remove"
                    >
                      {filled.length ? (
                        filled.slice(0, 3).map(([col, v]) => (
                          <Typography key={col} typography="s2" noWrap>
                            <Box
                              component="span"
                              sx={{ color: "text.secondary" }}
                            >
                              {columnName(col)}:{" "}
                            </Box>
                            {shortValue(v)}
                          </Typography>
                        ))
                      ) : (
                        <Typography typography="s2" color="text.secondary">
                          Blank row
                        </Typography>
                      )}
                      {item.splitIds.length > 0 && (
                        <Box sx={{ mt: 0.5 }}>
                          <SplitDelta
                            add={item.splitIds}
                            remove={[]}
                            splitOf={splitOf}
                          />
                        </Box>
                      )}
                    </ChangeItem>
                  );
                }
                if (item.kind === "status")
                  return (
                    <ChangeItem
                      key={`${item.opId}-${g.rowId}`}
                      label="Status"
                      onRevert={() => cs.revert(item.opId)}
                    >
                      <Typography typography="s2">
                        {ROW_STATUS_LABEL[item.status] ?? item.status}
                        {item.reason ? (
                          <Box
                            component="span"
                            sx={{ color: "text.secondary" }}
                          >
                            {` · ${item.reason}`}
                          </Box>
                        ) : null}
                      </Typography>
                    </ChangeItem>
                  );
                if (item.kind === "tags")
                  return (
                    <ChangeItem
                      key={`${item.opId}-${g.rowId}`}
                      label="Tags"
                      onRevert={() => cs.revert(item.opId)}
                    >
                      <Typography typography="s2">
                        {[
                          item.add.length ? `+ ${item.add.join(", ")}` : null,
                          item.remove.length
                            ? `− ${item.remove.join(", ")}`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </Typography>
                    </ChangeItem>
                  );
                if (item.kind === "deleted") {
                  const row = rowsById.get(g.rowId);
                  const first = row
                    ? (lookup.data?.columns ?? [])
                        .map((c) => cellText(row[c.id]))
                        .find((v) => v)
                    : null;
                  return (
                    <ChangeItem
                      key={`${item.opId}-${g.rowId}`}
                      label="Deleted"
                      onRevert={() => cs.revert(g.rowId)}
                      revertLabel="Restore"
                    >
                      <Typography
                        typography="s2"
                        color="text.secondary"
                        sx={{ textDecoration: "line-through" }}
                        noWrap
                      >
                        {first ? shortValue(first) : "Row and its values"}
                      </Typography>
                    </ChangeItem>
                  );
                }
                return null;
              })}
            </Box>
          ))}
        </ChangeList>

        <Typography sx={{ ...sectionLabelSx, mt: 2, mb: 0.75 }}>
          Description
        </Typography>
        <TextField
          fullWidth
          size="small"
          multiline
          minRows={2}
          maxRows={4}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={autoDescription}
          helperText={
            description.trim()
              ? " "
              : "Leave it empty to save the suggested description."
          }
          inputProps={{ maxLength: 500, "aria-label": "Description" }}
          // RV#36: focus lands in the description; ⌘↵ saves from here.
          autoFocus
        />

        <Typography sx={{ ...sectionLabelSx, mt: 1, mb: 0.75 }}>
          Tag this version (optional)
        </Typography>
        <Autocomplete
          freeSolo
          size="small"
          options={allTags(versions)}
          inputValue={tag}
          onInputChange={(_, v) => setTag((v ?? "").toLowerCase())}
          renderInput={(params) => (
            <TextField
              {...params}
              placeholder="e.g. golden-v4"
              error={Boolean(tagProblem)}
              helperText={
                tagHelp ??
                "A tag names one version. Experiments keep the version they ran on."
              }
              inputProps={{ ...params.inputProps, "aria-label": "Tag" }}
            />
          )}
        />
      </DialogContent>

      <DialogActions sx={{ px: 2, pb: 2, pt: 1 }}>
        <Typography typography="s3" color="text.disabled" sx={{ mr: "auto" }}>
          {createdCols.length
            ? `Generated columns are not versioned; ${createdColumnsText(
                createdCols,
              )} ${createdCols.length === 1 ? "was" : "were"} added now and stay${
                createdCols.length === 1 ? "s" : ""
              } as ${createdCols.length === 1 ? "it is" : "they are"}.`
            : "Generated columns are not versioned."}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onClose}
          disabled={save.isPending}
          sx={{ px: 3 }}
        >
          Cancel
        </Button>
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          disableInteractive
          title={
            <KeyHintTitle
              title={`Save v${nextNumber ?? ""} with these changes`}
              keys={["mod+enter"]}
            />
          }
        >
          <span>
            <LoadingButton
              size="small"
              variant="contained"
              color="primary"
              loading={save.isPending}
              disabled={!cs.hasPending || Boolean(tagProblem) || conflict}
              onClick={doSave}
              sx={{ px: 2, gap: 1 }}
              data-testid="save-version-submit"
            >
              Save version
              <KeyCap
                sx={{
                  height: 18,
                  minWidth: 18,
                  px: 0.5,
                  typography: "s3",
                  bgcolor: "transparent",
                  borderColor: "currentColor",
                  color: "inherit",
                  opacity: 0.8,
                }}
              >
                {localiseCap("⌘↵")}
              </KeyCap>
            </LoadingButton>
          </span>
        </CustomTooltip>
      </DialogActions>
    </Dialog>
  );
};

SaveVersionDialog.propTypes = {
  open: PropTypes.bool,
  datasetId: PropTypes.string,
  onClose: PropTypes.func,
  onSaved: PropTypes.func,
};

export default SaveVersionDialog;
