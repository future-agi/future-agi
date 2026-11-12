import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import TextDiff, {
  TextDiffLegend,
} from "src/sections/common/ValueViewer/TextDiff";
import { useVersionChanges } from "src/api/develop/dataset-versions";
import { ChangeItem, ChangeList, KindBadge, SplitDelta } from "./ChangeParts";
import { changesTitle, shortValue } from "./versionText";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "modified", label: "Edited" },
  { key: "added", label: "Added" },
  { key: "deleted", label: "Deleted" },
  { key: "splits", label: "Split changes" },
];

const isJsonish = (v) =>
  typeof v === "object" ||
  (typeof v === "string" && /^\s*[[{]/.test(v) && /[\]}]\s*$/.test(v));

const RowChange = ({ row, splitOf }) => {
  const add = (row.splits?.after ?? []).filter(
    (s) => !(row.splits?.before ?? []).includes(s),
  );
  const remove = (row.splits?.before ?? []).filter(
    (s) => !(row.splits?.after ?? []).includes(s),
  );
  const cells = Object.entries(row.cells ?? {});
  return (
    <Box sx={{ px: 1.5, py: 1 }}>
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          {row.row_label}
        </Typography>
        <KindBadge kind={row.kind} />
        <Typography typography="s3" color="text.secondary">
          {row.kind === "added"
            ? `added in v${row.version}`
            : row.kind === "deleted"
              ? `deleted in v${row.version}`
              : row.version
                ? `changed in v${row.version}`
                : ""}
        </Typography>
      </Stack>
      {cells.map(([col, c]) => (
        <ChangeItem key={col} label={c.column_name}>
          <TextDiff
            from={c.before ?? ""}
            to={c.after ?? ""}
            kind={isJsonish(c.before) || isJsonish(c.after) ? "json" : "words"}
          />
        </ChangeItem>
      ))}
      {row.preview && (
        <ChangeItem label={row.preview.column_name}>
          <Typography
            typography="s2"
            color={row.kind === "deleted" ? "text.secondary" : "text.primary"}
            sx={{
              textDecoration: row.kind === "deleted" ? "line-through" : "none",
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {shortValue(row.preview.value, 400)}
          </Typography>
        </ChangeItem>
      )}
      {(add.length > 0 || remove.length > 0) && (
        <ChangeItem label="Splits">
          <SplitDelta add={add} remove={remove} splitOf={splitOf} />
        </ChangeItem>
      )}
    </Box>
  );
};

RowChange.propTypes = {
  row: PropTypes.object.isRequired,
  splitOf: PropTypes.func.isRequired,
};

/**
 * The net changes of one version (`against="parent"`), since a version
 * (`against="latest"`) or between two versions (`against=<version id>`),
 * N4. Rows are grouped by kind with per-cell word diffs and split deltas.
 */
const VersionChangesDialog = ({
  open,
  datasetId,
  versionId,
  against = "parent",
  title,
  subtitle,
  onClose,
}) => {
  const [filter, setFilter] = useState("all");
  useEffect(() => {
    if (open) setFilter("all");
  }, [open, versionId, against]);
  const { data, isLoading, isError } = useVersionChanges(datasetId, versionId, {
    against,
    enabled: open,
  });
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const splitDefs = useMemo(
    () => new Map((data?.split_defs ?? []).map((s) => [s.id, s])),
    [data],
  );
  const splitOf = (id) => splitDefs.get(id) ?? { id, name: id, color: "grey" };
  const countOf = (key) =>
    key === "all" ? rows.length : rows.filter((r) => r.kind === key).length;
  const shown = filter === "all" ? rows : rows.filter((r) => r.kind === filter);
  const anyDiff = rows.some((r) => Object.keys(r.cells ?? {}).length);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { width: 880, maxWidth: "calc(100vw - 32px)" } }}
    >
      <DialogTitle sx={{ p: 2, pb: 1 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Typography typography="m2" fontWeight="fontWeightBold">
            {title}
          </Typography>
          <Box sx={{ flex: 1 }} />
          <IconButton onClick={onClose} aria-label="Close">
            <Iconify icon="mdi:close" />
          </IconButton>
        </Stack>
        <Typography typography="s1" color="text.primary" sx={{ mt: 0.25 }}>
          {data ? changesTitle(data) : " "}
        </Typography>
        {subtitle && (
          <Typography typography="s2" color="text.secondary" sx={{ mt: 0.25 }}>
            {subtitle}
          </Typography>
        )}
      </DialogTitle>
      <DialogContent sx={{ px: 2, pb: 2 }}>
        <Stack direction="row" alignItems="center" gap={0.75} sx={{ mb: 1 }}>
          {FILTERS.filter((f) => f.key === "all" || countOf(f.key) > 0).map(
            (f) => (
              <Chip
                key={f.key}
                size="small"
                label={`${f.label} · ${countOf(f.key)}`}
                variant={filter === f.key ? "filled" : "outlined"}
                color={filter === f.key ? "primary" : "default"}
                onClick={() => setFilter(f.key)}
                sx={{ borderRadius: 0.5, typography: "s2" }}
              />
            ),
          )}
          <Box sx={{ flex: 1 }} />
          {anyDiff && <TextDiffLegend />}
        </Stack>
        {isLoading && (
          <ChangeList maxHeight={520}>
            {[0, 1, 2].map((i) => (
              <Box key={i} sx={{ px: 1.5, py: 1.25 }}>
                <Skeleton width={120} height={20} />
                <Skeleton height={18} />
                <Skeleton width="70%" height={18} />
              </Box>
            ))}
          </ChangeList>
        )}
        {isError && (
          <Typography typography="s1" color="error.main">
            Couldn&apos;t load the changes. Close and try again.
          </Typography>
        )}
        {data && !rows.length && (
          <Typography typography="s1" color="text.secondary" sx={{ py: 2 }}>
            No row changes between these versions.
          </Typography>
        )}
        {data && rows.length > 0 && (
          <ChangeList maxHeight={520}>
            {shown.map((row) => (
              <RowChange key={row.row_id} row={row} splitOf={splitOf} />
            ))}
          </ChangeList>
        )}
      </DialogContent>
    </Dialog>
  );
};

VersionChangesDialog.propTypes = {
  open: PropTypes.bool,
  datasetId: PropTypes.string,
  versionId: PropTypes.string,
  against: PropTypes.string,
  title: PropTypes.node,
  subtitle: PropTypes.node,
  onClose: PropTypes.func,
};

export default VersionChangesDialog;
