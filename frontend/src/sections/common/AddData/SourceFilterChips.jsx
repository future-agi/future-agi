import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { plural } from "src/sections/common/vocab";
import { RESOLUTION_FILTERS } from "./shapes";
import { IMPORT_WINDOW_OPTIONS, windowChipLabel } from "./mappingModel";

const isoDay = (ms) => {
  const d = new Date(ms);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

/** W3-5: `Custom range…` → `{from, to}` (local days, inclusive). */
function CustomRangeDialog({ open, value, onClose, onApply }) {
  const today = Date.now();
  const [from, setFrom] = useState(
    value?.from ? isoDay(Date.parse(value.from)) : isoDay(today - 30 * 864e5),
  );
  const [to, setTo] = useState(
    value?.to ? isoDay(Date.parse(value.to)) : isoDay(today),
  );
  const bad = !from || !to || from > to;
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Custom range</DialogTitle>
      <DialogContent>
        <Stack direction="row" gap={1.5} sx={{ pt: 1 }}>
          <TextField
            type="date"
            size="small"
            label="From"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            InputLabelProps={{ shrink: true }}
            inputProps={{ "data-testid": "custom-range-from", max: to }}
            fullWidth
          />
          <TextField
            type="date"
            size="small"
            label="To"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            InputLabelProps={{ shrink: true }}
            inputProps={{ "data-testid": "custom-range-to", min: from }}
            fullWidth
          />
        </Stack>
        {bad ? (
          <Typography typography="s3" color="error.main" sx={{ mt: 1 }}>
            Pick a From day on or before the To day.
          </Typography>
        ) : null}
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={bad}
          data-testid="custom-range-apply"
          onClick={() =>
            onApply({
              from: new Date(`${from}T00:00:00`).toISOString(),
              to: new Date(`${to}T23:59:59.999`).toISOString(),
            })
          }
        >
          Apply
        </Button>
      </DialogActions>
    </Dialog>
  );
}
CustomRangeDialog.propTypes = {
  open: PropTypes.bool,
  value: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
};

const chipSx = {
  height: 24,
  typography: "s2",
  bgcolor: "background.paper",
  "& .MuiChip-deleteIcon": { fontSize: 16 },
};

/**
 * IT-R1 G5 P4: the production filter as chips at the top of the import
 * sheet's middle column (was a truncated line under the source, edited from
 * More ▾): `support-prod ▾` `Last 7 days` `Resolution = Fail ×` `+ Filter`.
 * A picked set of sessions / traces shows as one chip whose × goes back to
 * the filter. Only what the import can change is removable.
 */
const SourceFilterChips = ({
  sourceKind,
  projects = [],
  projectId,
  onProject,
  resolution,
  onResolution,
  pinnedCount = 0,
  onClearPinned,
  count,
  rangeDays = 7,
  windowId = null,
  onWindow,
}) => {
  const [projectMenu, setProjectMenu] = useState(null);
  const [windowMenu, setWindowMenu] = useState(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [addMenu, setAddMenu] = useState(null);
  if (sourceKind !== "sessions" && sourceKind !== "traces") return null;
  const unit = sourceKind === "sessions" ? "session" : "trace";
  const project = projects.find((p) => p.id === projectId);
  const res = RESOLUTION_FILTERS.find((r) => r.id === resolution);
  const filtered = res && res.id !== "any";

  if (pinnedCount)
    return (
      <Stack direction="row" gap={0.75} alignItems="center" flexWrap="wrap">
        <Chip
          size="small"
          variant="outlined"
          label={`${plural(pinnedCount, `picked ${unit}`)} from ${project?.name ?? "this project"}`}
          onDelete={onClearPinned}
          deleteIcon={
            <CustomTooltip show title="Use a filter instead" size="small">
              <Iconify icon="mingcute:close-line" />
            </CustomTooltip>
          }
          sx={chipSx}
          data-testid="add-data-filter-pinned"
        />
      </Stack>
    );

  return (
    <Stack
      direction="row"
      gap={0.75}
      alignItems="center"
      flexWrap="wrap"
      data-testid="add-data-filter-chips"
    >
      <Typography typography="s2" color="text.secondary" sx={{ mr: 0.25 }}>
        Filter
      </Typography>
      <Chip
        size="small"
        variant="outlined"
        label={project?.name ?? "Project"}
        onClick={(e) => setProjectMenu(e.currentTarget)}
        onDelete={(e) =>
          setProjectMenu(e.currentTarget.closest(".MuiChip-root"))
        }
        deleteIcon={<Iconify icon="mdi:chevron-down" />}
        sx={chipSx}
        aria-haspopup="menu"
      />
      <Menu
        anchorEl={projectMenu}
        open={Boolean(projectMenu)}
        onClose={() => setProjectMenu(null)}
      >
        {projects.map((p) => (
          <MenuItem
            key={p.id}
            selected={p.id === projectId}
            onClick={() => {
              setProjectMenu(null);
              onProject?.(p.id);
            }}
            sx={{ typography: "s2" }}
          >
            {p.name}
          </MenuItem>
        ))}
      </Menu>
      {/* W3-5: the window is a choice (24 h · 7 · 14 · 30 days). */}
      <Chip
        size="small"
        variant="outlined"
        label={windowChipLabel(windowId) ?? `Last ${rangeDays} days`}
        onClick={onWindow ? (e) => setWindowMenu(e.currentTarget) : undefined}
        deleteIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        onDelete={onWindow ? (e) => setWindowMenu(e.currentTarget) : undefined}
        sx={chipSx}
        data-testid="add-data-filter-window"
        aria-haspopup="menu"
      />
      <Menu
        anchorEl={windowMenu}
        open={Boolean(windowMenu)}
        onClose={() => setWindowMenu(null)}
      >
        {IMPORT_WINDOW_OPTIONS.map((w) => (
          <MenuItem
            key={w.id}
            selected={w.id === windowId}
            onClick={() => {
              setWindowMenu(null);
              onWindow?.(w.id);
            }}
            sx={{ typography: "s2" }}
          >
            {w.label}
          </MenuItem>
        ))}
        <MenuItem
          selected={Boolean(windowId) && typeof windowId === "object"}
          onClick={() => {
            setWindowMenu(null);
            setCustomOpen(true);
          }}
          sx={{ typography: "s2" }}
        >
          Custom range…
        </MenuItem>
      </Menu>
      {customOpen ? (
        <CustomRangeDialog
          open
          value={typeof windowId === "object" ? windowId : null}
          onClose={() => setCustomOpen(false)}
          onApply={(range) => {
            setCustomOpen(false);
            onWindow?.(range);
          }}
        />
      ) : null}
      {filtered ? (
        <Chip
          size="small"
          variant="outlined"
          color="primary"
          label={res.label}
          onDelete={() => onResolution("any")}
          sx={chipSx}
          data-testid="add-data-filter-resolution"
        />
      ) : null}
      {sourceKind === "sessions" || sourceKind === "traces" ? (
        <>
          <Chip
            size="small"
            variant="outlined"
            label="+ Filter"
            onClick={(e) => setAddMenu(e.currentTarget)}
            sx={{ ...chipSx, borderStyle: "dashed", color: "text.secondary" }}
            aria-haspopup="menu"
          />
          <Menu
            anchorEl={addMenu}
            open={Boolean(addMenu)}
            onClose={() => setAddMenu(null)}
          >
            {RESOLUTION_FILTERS.filter((r) => r.id !== "any").map((r) => (
              <MenuItem
                key={r.id}
                disabled={resolution === r.id}
                onClick={() => {
                  setAddMenu(null);
                  onResolution(r.id);
                }}
                sx={{ typography: "s2" }}
              >
                {r.label}
              </MenuItem>
            ))}
          </Menu>
        </>
      ) : null}
      {Number.isFinite(count) ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="add-data-match-count"
        >
          {`· ${plural(count, unit)} ${count === 1 ? "matches" : "match"}`}
        </Typography>
      ) : null}
    </Stack>
  );
};

SourceFilterChips.propTypes = {
  sourceKind: PropTypes.string,
  projects: PropTypes.array,
  projectId: PropTypes.string,
  onProject: PropTypes.func,
  resolution: PropTypes.string,
  onResolution: PropTypes.func,
  pinnedCount: PropTypes.number,
  onClearPinned: PropTypes.func,
  count: PropTypes.number,
  rangeDays: PropTypes.number,
  windowId: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  onWindow: PropTypes.func,
};

export default SourceFilterChips;
