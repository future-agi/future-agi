import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Divider,
  InputAdornment,
  MenuItem,
  Popover,
  TextField,
  Typography,
  useTheme,
  alpha,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useDatasetSplits } from "src/api/develop/dataset-splits";
import { splitChipColors } from "src/sections/common/Datasets/splitColors";
import { NO_SPLIT, splitButtonLabel } from "../DataGrid/dataGridView";
import { useDataGridView } from "../DataGrid/useDataGridView";
import { useManageSplitsStore } from "./splitStores";
import SplitCatalogState from "./SplitCatalogState";
import { isSurfaceShown } from "src/launch/launchSwitch";

const Row = ({ checked, onToggle, dot, label, count }) => (
  <MenuItem
    dense
    onClick={onToggle}
    sx={{ gap: 1, py: 0.5, px: 1, borderRadius: 0.5, typography: "s2" }}
  >
    <Checkbox
      size="small"
      checked={checked}
      tabIndex={-1}
      disableRipple
      sx={{ p: 0 }}
    />
    <Box
      sx={{
        width: 8,
        height: 8,
        borderRadius: "50%",
        bgcolor: dot ?? "transparent",
        border: dot ? "none" : "1px dashed",
        borderColor: "text.disabled",
        flexShrink: 0,
      }}
    />
    <Typography typography="s2" noWrap sx={{ flex: 1, minWidth: 0 }}>
      {label}
    </Typography>
    <Typography
      typography="s3"
      color="text.secondary"
      sx={{ fontVariantNumeric: "tabular-nums" }}
    >
      {count}
    </Typography>
  </MenuItem>
);

Row.propTypes = {
  checked: PropTypes.bool,
  onToggle: PropTypes.func,
  dot: PropTypes.string,
  label: PropTypes.string,
  count: PropTypes.number,
};

/**
 * `Splits` toolbar filter (area-datasets §6): `All splits` / `hard, smoke` /
 * `3 splits`. Selections OR into `system:splits in [ids]`; `No split` matches
 * rows without one. URL: `splits=hard,smoke`.
 */
// L6 launch switch: splits are absent on dev (datasets/{id}/splits/), so a
// launch build has no Splits filter until a lane ships them.
const SplitFilterButton = (props) =>
  isSurfaceShown("dataset.splits") ? (
    <SplitFilterButtonInner {...props} />
  ) : null;

const SplitFilterButtonInner = ({ datasetId, disabled }) => {
  const theme = useTheme();
  const [anchor, setAnchor] = useState(null);
  const [query, setQuery] = useState("");
  const { data, isPending, isError, isFetching, refetch } =
    useDatasetSplits(datasetId);
  const catalogUnavailable = isPending || isError;
  const { splitIds, setSplitIds } = useDataGridView(datasetId);
  const openManage = useManageSplitsStore((s) => s.setOpen);
  const splits = data?.splits ?? [];
  const active = splitIds.length > 0;

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? splits.filter((s) => s.name.toLowerCase().includes(q)) : splits;
  }, [splits, query]);

  const toggle = (id) =>
    setSplitIds(
      splitIds.includes(id)
        ? splitIds.filter((x) => x !== id)
        : [...splitIds, id],
    );

  return (
    <>
      <CustomTooltip
        show
        title="Filter rows by split"
        placement="bottom"
        size="small"
        arrow
      >
        <span>
          <Button
            size="small"
            variant="outlined"
            disabled={disabled}
            onClick={(e) => setAnchor(e.currentTarget)}
            startIcon={<Iconify icon="mdi:tag-multiple-outline" width={16} />}
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            sx={{
              px: 1.25,
              typography: "s2",
              color: active ? "primary.main" : "text.primary",
              borderColor: active ? "primary.main" : "divider",
              bgcolor: active
                ? alpha(theme.palette.primary.main, 0.12)
                : "transparent",
              maxWidth: 200,
              "& .MuiButton-startIcon": { mr: 0.5 },
              "& .MuiButton-endIcon": { ml: 0.25 },
            }}
          >
            <Box
              component="span"
              sx={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {splitButtonLabel(splitIds, data?.byId)}
            </Box>
          </Button>
        </span>
      </CustomTooltip>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => {
          setAnchor(null);
          setQuery("");
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: -4, horizontal: "left" }}
        PaperProps={{ sx: { width: 260, p: 0.5 } }}
      >
        <Box sx={{ p: 0.5 }}>
          <TextField
            size="small"
            fullWidth
            autoFocus
            placeholder="Find split…"
            disabled={catalogUnavailable}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
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
        <Box sx={{ maxHeight: 260, overflowY: "auto", py: 0.5 }}>
          {catalogUnavailable ? (
            <SplitCatalogState
              isError={isError}
              isFetching={isFetching}
              onRetry={() => refetch()}
            />
          ) : (
            <>
              {shown.map((s) => (
                <Row
                  key={s.id}
                  checked={splitIds.includes(s.id)}
                  onToggle={() => toggle(s.id)}
                  dot={splitChipColors(s.color, theme).dot}
                  label={s.name}
                  count={s.row_count}
                />
              ))}
              {!shown.length && query && (
                <Typography
                  typography="s2"
                  color="text.disabled"
                  sx={{ px: 1.5, py: 1 }}
                >
                  No split matches “{query}”
                </Typography>
              )}
              {!query && (
                <Row
                  checked={splitIds.includes(NO_SPLIT)}
                  onToggle={() => toggle(NO_SPLIT)}
                  label="No split"
                  count={data?.no_split_count ?? 0}
                />
              )}
            </>
          )}
        </Box>
        <Divider sx={{ my: 0.5 }} />
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            px: 0.5,
            pb: 0.25,
          }}
        >
          <Button
            size="small"
            startIcon={<Iconify icon="mdi:cog-outline" width={16} />}
            onClick={() => {
              setAnchor(null);
              openManage(true);
            }}
            sx={{ typography: "s2", color: "text.secondary" }}
          >
            Manage splits…
          </Button>
          {active && (
            <Button
              size="small"
              onClick={() => setSplitIds([])}
              sx={{ typography: "s2" }}
            >
              Clear
            </Button>
          )}
        </Box>
      </Popover>
    </>
  );
};

SplitFilterButtonInner.propTypes = {
  datasetId: PropTypes.string.isRequired,
  disabled: PropTypes.bool,
};

SplitFilterButton.propTypes = SplitFilterButtonInner.propTypes;

export default SplitFilterButton;
