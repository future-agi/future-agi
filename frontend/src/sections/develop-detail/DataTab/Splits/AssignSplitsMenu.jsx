import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  CircularProgress,
  Divider,
  InputAdornment,
  MenuItem,
  Popover,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useAssignSplits,
  useCreateSplit,
  useDatasetSplits,
} from "src/api/develop/dataset-splits";
import {
  nextSplitColor,
  splitChipColors,
} from "src/sections/common/Datasets/splitColors";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { notify } from "src/sections/common/ux/toast";
import { fmtCount } from "src/sections/common/format/fmt";
import { splitCheckState } from "../DataGrid/dataGridView";
import { useManageSplitsStore } from "./splitStores";
import SplitCatalogState from "./SplitCatalogState";

export const SPLIT_NAME_RE = /^(?!none$)[a-z0-9][a-z0-9._-]{0,29}$/;

/**
 * Assign splits to rows (area-datasets §6). Opened from the bulk bar, the
 * cell menu and the Row view's `+ Split` chip.
 *
 * Tri-state: checked when every row has the split, indeterminate when some
 * do; a click adds the split to all rows (or removes it when all have it).
 * The change is a pending `set_splits` op in the change-set; a new name
 * offers `Create split “edge-cases”`, which creates it right away.
 *
 * @param {object} props
 * @param {Element|null} props.anchorEl
 * @param {() => void} props.onClose
 * @param {string} props.datasetId
 * @param {string[]} props.rowIds         rows to assign
 * @param {string[][]} props.rowSplits    saved split ids per row (same order)
 * @param {boolean} [props.loading]       rows still resolving (select-all)
 */
const AssignSplitsMenu = ({
  anchorEl,
  onClose,
  datasetId,
  rowIds,
  rowSplits,
  loading = false,
  anchorOrigin = { vertical: "bottom", horizontal: "left" },
}) => {
  const theme = useTheme();
  const [query, setQuery] = useState("");
  const { data, isPending, isError, isFetching, refetch } =
    useDatasetSplits(datasetId);
  const catalogUnavailable = isPending || isError;
  const { refreshGrid } = useDevelopDetailContext();
  const { mutateAsync: assign, isPending: saving } = useAssignSplits(datasetId);
  const { mutateAsync: createSplit, isPending: creating } =
    useCreateSplit(datasetId);
  const openManage = useManageSplitsStore((s) => s.setOpen);
  const splits = useMemo(() => data?.splits ?? [], [data?.splits]);
  const effective = (rowIds ?? []).map((id, i) => rowSplits?.[i] ?? []);

  const q = query.trim().toLowerCase();
  const shown = q ? splits.filter((s) => s.name.includes(q)) : splits;
  const exact = splits.some((s) => s.name === q);
  const canCreate = !catalogUnavailable && q && !exact && SPLIT_NAME_RE.test(q);
  const n = rowIds?.length ?? 0;

  const hitsFor = (splitId) =>
    effective.filter((ids) => ids.includes(splitId)).length;

  const apply = async (split, state) => {
    if (catalogUnavailable || !n || loading || saving || creating) return;
    const remove = state === "all";
    try {
      await assign({
        row_ids: rowIds,
        add: remove ? [] : [split.id],
        remove: remove ? [split.id] : [],
      });
      refreshGrid?.();
      notify(`Saved split membership for ${fmtCount(n, "row")}`, {
        key: "split-assign",
        variant: "success",
      });
      onClose?.();
    } catch {
      // The mutation displays the server's error; no local membership is changed.
    }
  };

  const create = async () => {
    if (!canCreate || !n || saving || creating || loading) return;
    try {
      const created = await createSplit({
        name: q,
        color: nextSplitColor(splits),
      });
      if (created?.id) {
        await assign({ row_ids: rowIds, add: [created.id], remove: [] });
        refreshGrid?.();
        notify(
          `Created split “${created.name}” and saved ${fmtCount(n, "row")}`,
          { key: "split-create", variant: "success" },
        );
        setQuery("");
        onClose?.();
      }
    } catch {
      // useCreateSplit shows the error
    }
  };

  return (
    <Popover
      open={Boolean(anchorEl)}
      anchorEl={anchorEl}
      onClose={() => {
        setQuery("");
        onClose?.();
      }}
      anchorOrigin={anchorOrigin}
      transformOrigin={{ vertical: -4, horizontal: "left" }}
      onClick={(e) => e.stopPropagation()}
      PaperProps={{ sx: { width: 260, p: 0.5 } }}
    >
      <Box sx={{ px: 1, pt: 0.75, pb: 0.5 }}>
        <Typography typography="s3" color="text.secondary">
          {loading
            ? "Resolving selected rows…"
            : `Assign split to ${fmtCount(n, "row")}`}
        </Typography>
        {/* RV#40: say when the change lands. */}
        <Typography
          typography="s3"
          color="text.disabled"
          data-testid="split-menu-subtitle"
        >
          {saving ? "Saving membership…" : "Changes are saved immediately"}
        </Typography>
      </Box>
      <Box sx={{ p: 0.5 }}>
        <TextField
          size="small"
          fullWidth
          autoFocus
          placeholder="Find or create a split…"
          disabled={catalogUnavailable}
          value={query}
          onChange={(e) => setQuery(e.target.value.toLowerCase())}
          onKeyDown={(e) => {
            // Esc closes the menu; other keys stay in the field.
            if (e.key === "Escape") {
              setQuery("");
              onClose?.();
              return;
            }
            e.stopPropagation();
            if (e.key === "Enter") {
              e.preventDefault();
              if (canCreate) create();
              else if (shown.length === 1)
                apply(shown[0], splitCheckState(effective, shown[0].id));
            }
          }}
          InputProps={{
            sx: { typography: "s2", height: 30 },
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="mdi:magnify"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
        />
      </Box>
      <Box sx={{ maxHeight: 260, overflowY: "auto", py: 0.25 }}>
        {catalogUnavailable ? (
          <SplitCatalogState
            isError={isError}
            isFetching={isFetching}
            onRetry={() => refetch()}
          />
        ) : loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={18} />
          </Box>
        ) : (
          shown.map((s) => {
            const state = splitCheckState(effective, s.id);
            return (
              <MenuItem
                key={s.id}
                dense
                onClick={() => apply(s, state)}
                disabled={saving || creating || !n}
                sx={{ gap: 1, py: 0.5, px: 1, borderRadius: 0.5 }}
              >
                <Checkbox
                  size="small"
                  checked={state === "all"}
                  indeterminate={state === "some"}
                  tabIndex={-1}
                  disableRipple
                  sx={{ p: 0 }}
                />
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    bgcolor: splitChipColors(s.color, theme).dot,
                    flexShrink: 0,
                  }}
                />
                <Typography typography="s2" noWrap sx={{ flex: 1 }}>
                  {s.name}
                </Typography>
                {state !== "some" && Number.isFinite(s.row_count) && (
                  <Typography
                    typography="s3"
                    color="text.disabled"
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                    title={`${fmtCount(s.row_count, "row")} in ${s.name}`}
                  >
                    {fmtCount(s.row_count)}
                  </Typography>
                )}
                {state === "some" && (
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    {hitsFor(s.id)} of {n}
                  </Typography>
                )}
              </MenuItem>
            );
          })
        )}
        {canCreate && (
          <MenuItem
            dense
            onClick={create}
            disabled={creating || !n}
            sx={{
              gap: 1,
              py: 0.75,
              px: 1,
              borderRadius: 0.5,
            }}
          >
            <Iconify icon="mdi:plus" width={16} />
            <Typography typography="s2" color="inherit">
              Create split “{q}”
            </Typography>
          </MenuItem>
        )}
        {!catalogUnavailable && q && !canCreate && !exact && (
          <Typography
            typography="s3"
            color="text.disabled"
            sx={{ px: 1.5, py: 1 }}
          >
            Use lowercase letters, digits, dots, dashes or underscores.
          </Typography>
        )}
      </Box>
      <Divider sx={{ my: 0.5 }} />
      <MenuItem
        dense
        onClick={() => {
          onClose?.();
          openManage(true);
        }}
        sx={{
          gap: 1,
          py: 0.5,
          px: 1,
          borderRadius: 0.5,
        }}
      >
        <Iconify icon="mdi:cog-outline" width={16} />
        <Typography typography="s2" color="inherit">
          Manage splits…
        </Typography>
      </MenuItem>
    </Popover>
  );
};

AssignSplitsMenu.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  datasetId: PropTypes.string.isRequired,
  rowIds: PropTypes.arrayOf(PropTypes.string),
  rowSplits: PropTypes.arrayOf(PropTypes.arrayOf(PropTypes.string)),
  loading: PropTypes.bool,
  anchorOrigin: PropTypes.object,
};

export default AssignSplitsMenu;
