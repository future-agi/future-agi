import PropTypes from "prop-types";
import { Box, Stack, Typography, Popover, Select, MenuItem, Tab } from "@mui/material";

import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { ROW_HEIGHTS, SCENARIOS_COPY } from "./scenarios.constants";

// Per-axis icon, keyed by the server grouping `value`. Falls back to a neutral
// icon for any axis the backend adds later.
const GROUPING_ICONS = {
  goal: "solar:target-linear",
  sub_goal: "solar:map-linear",
  accent: "solar:soundwave-linear",
  age: "solar:user-rounded-linear",
  attack: "solar:shield-warning-linear",
  task: "solar:checklist-minimalistic-linear",
  "": "solar:list-linear",
};
const groupingIcon = (value) => GROUPING_ICONS[value] ?? "solar:list-linear";

// Tighter than the toolbar's own segmented control: these sit in a small panel.
const compactTabsSx = {
  minHeight: 28,
  "& .MuiTab-root": { minHeight: 24, px: 1, fontSize: 12 },
};

const VIEWS = [
  { value: "table", label: "Table" },
  { value: "list", label: "List" },
];

// One labelled line of the panel.
function Row({ label, children }) {
  return (
    <Stack direction="row" alignItems="center" spacing={2}>
      <Typography sx={{ typography: "s2", color: "text.subtitle", width: 76, flexShrink: 0 }}>{label}</Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}
Row.propTypes = { label: PropTypes.string, children: PropTypes.node };

// The toolbar's Display panel: how the list is shown, not what is in it. View
// and Row height are short fixed sets, so segmented tabs; the grouping axes come
// from the server and can grow, so they sit in one dropdown and the panel keeps
// its size however many there are. Stays open while the user adjusts.
export default function DisplayPanel({
  anchorEl, onClose,
  view, onViewChange,
  groupBy, onGroupByChange, groupings = [],
  rowHeight = "Short", onRowHeightChange,
}) {
  // The server's own default stands until one is picked. "No grouping" is the
  // empty value, so the Select must render it rather than read it as unset.
  const active = groupings.find((g) => g.value === groupBy) || groupings[0];
  return (
    <Popover
      open={!!anchorEl}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      // A fixed width, and every row always rendered, so switching the view
      // never resizes the panel under the pointer.
      slotProps={{ paper: { role: "dialog", "aria-label": SCENARIOS_COPY.displayLabel, sx: { mt: 0.5, p: 2, width: 380 } } }}
    >
      <Stack spacing={1.5}>
        <Row label={SCENARIOS_COPY.viewLabel}>
          <SegmentedTabs value={view} onChange={(_, v) => onViewChange(v)} sx={compactTabsSx}>
            {VIEWS.map((v) => <Tab key={v.value} value={v.value} label={v.label} />)}
          </SegmentedTabs>
        </Row>
        {groupings.length > 0 && (
          <Row label={SCENARIOS_COPY.groupByLabel}>
            <Select
              size="small"
              fullWidth
              displayEmpty
              value={active?.value ?? ""}
              onChange={(e) => onGroupByChange?.(e.target.value)}
              SelectDisplayProps={{ "aria-label": SCENARIOS_COPY.groupByLabel }}
              renderValue={() => (
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Iconify icon={groupingIcon(active?.value)} width={14} sx={{ color: "text.subtitle" }} />
                  <span>{active?.label}</span>
                </Stack>
              )}
              sx={{ typography: "s2", height: 30 }}
              MenuProps={{ slotProps: { paper: { sx: { maxHeight: 320 } } } }}
            >
              {groupings.map((g) => (
                <MenuItem key={g.value} value={g.value} sx={{ typography: "s2", gap: 1 }}>
                  <Iconify icon={groupingIcon(g.value)} width={14} sx={{ color: "text.subtitle" }} />
                  {g.label}
                </MenuItem>
              ))}
            </Select>
          </Row>
        )}
        {onRowHeightChange && (
          // The list view has no rows to size; the control stays, disabled.
          <Row label={SCENARIOS_COPY.rowHeight}>
            <SegmentedTabs
              value={rowHeight}
              onChange={(_, v) => onRowHeightChange(v)}
              sx={{ ...compactTabsSx, ...(view !== "table" && { opacity: 0.5 }) }}
            >
              {Object.keys(ROW_HEIGHTS).map((h) => (
                <Tab key={h} value={h} label={h} disabled={view !== "table"} />
              ))}
            </SegmentedTabs>
          </Row>
        )}
      </Stack>
    </Popover>
  );
}

DisplayPanel.propTypes = {
  anchorEl: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  view: PropTypes.string.isRequired,
  onViewChange: PropTypes.func.isRequired,
  groupBy: PropTypes.string,
  onGroupByChange: PropTypes.func,
  groupings: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.string }),
  ),
  rowHeight: PropTypes.string,
  onRowHeightChange: PropTypes.func,
};
