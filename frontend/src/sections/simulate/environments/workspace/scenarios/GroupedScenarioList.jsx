import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, IconButton, Tooltip, Checkbox } from "@mui/material";

import Iconify from "src/components/iconify";
import ScenarioDetail from "./ScenarioDetail";
import { SCENARIOS_COPY } from "./scenarios.constants";
import { ENV_SHAPE, GROUP_SHAPE } from "./scenarios.shapes";

// A seeded-from-template env is read-only until forked; the per-row edit/delete
// carry this on their tooltip while locked.
const LOCK_TOOLTIP = "Fork this environment to edit.";

// Match the table's monochrome checkbox so the two views read identically.
const selectableCheckboxSx = {
  p: 0,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

const onKeyActivate = (fn) => (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fn();
  }
};

// The grouped body: each use-case section as a collapsible block with a sticky
// header. The per-row edit pencil opens the editor through onEdit; remove works
// through onRemove.
export default function GroupedScenarioList({ groups, env, onEdit, onRemove, onHideGroup, selection, locked = false }) {
  return (
    <Box>
      {groups.map((g) => (
        <CollapsibleGroup key={g.id} group={g} env={env} onEdit={onEdit} onRemove={onRemove} onHideGroup={onHideGroup} selection={selection} locked={locked} />
      ))}
    </Box>
  );
}
GroupedScenarioList.propTypes = {
  groups: PropTypes.arrayOf(GROUP_SHAPE),
  env: ENV_SHAPE,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onHideGroup: PropTypes.func,
  // Predicate selection model (useSelection). The list view had no checkboxes
  // in the design; supplying this adds them, matching the table.
  selection: PropTypes.object,
  locked: PropTypes.bool,
};

function CollapsibleGroup({ group, env, onEdit, onRemove, onHideGroup, selection, locked = false }) {
  const [open, setOpen] = useState(true);

  // Selection is opt-in and drops out entirely on a locked template, mirroring
  // the table. The group-header checkbox selects just this group's visible rows.
  const selectable = !!selection && !locked;
  const groupIds = group.rows.map((r) => r.id).filter(Boolean);
  const groupState = selectable ? selection.pageState(groupIds) : null;

  return (
    <Box>
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        role="button" tabIndex={0}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyActivate(() => setOpen((o) => !o))}
        sx={{
          position: "sticky", top: 0, zIndex: 2, cursor: "pointer",
          px: 2.5, py: 1.75,
          bgcolor: "background.neutral",
          borderBottom: "1px solid", borderColor: "divider",
          borderTop: "1px solid", borderTopColor: "divider",
          "&:hover": {
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
          },
        }}
      >
        {selectable && (
          <Checkbox
            size="small"
            checked={groupState.allChecked}
            indeterminate={groupState.someChecked}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => selection.setPage(groupIds, e.target.checked)}
            aria-label={`Select group ${group.label}`}
            sx={{ ...selectableCheckboxSx, flexShrink: 0 }}
          />
        )}
        <Iconify
          icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
          width={15}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold", color: "text.primary", flex: 1, minWidth: 0 }}>
          {group.label}
        </Typography>
        <Typography
          sx={{
            px: 1, py: 0.25, borderRadius: 0.75,
            typography: "s3", fontWeight: "fontWeightBold", color: "text.secondary",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.06),
            fontVariantNumeric: "tabular-nums", flexShrink: 0, letterSpacing: 0.2,
          }}
        >
          {group.rows.length} {group.rows.length === 1 ? "scenario" : "scenarios"}
        </Typography>
        {onHideGroup && (
          // The header is itself a keyboard button; stop click and keydown from
          // bubbling so hiding a group doesn't also toggle the collapse (and so
          // the header's preventDefault doesn't swallow this button's activation).
          <Tooltip arrow title={SCENARIOS_COPY.hideGroup}>
            <IconButton
              size="small"
              aria-label={SCENARIOS_COPY.hideGroup}
              onClick={(e) => { e.stopPropagation(); onHideGroup(group.id); }}
              onKeyDown={(e) => e.stopPropagation()}
              sx={{ flexShrink: 0, color: "text.subtitle", "&:hover": { color: "text.primary" } }}
            >
              <Iconify icon="solar:eye-closed-linear" width={15} />
            </IconButton>
          </Tooltip>
        )}
      </Stack>

      {open && (
        <Box sx={{ pl: 3 }}>
          <Stack
            divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
            sx={{
              borderLeft: "2px solid",
              borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
            }}
          >
            {group.rows.map((s) => (
              <Stack key={s.id} direction="row" alignItems="flex-start">
                {selectable && (
                  <Box component="span" sx={{ display: "inline-flex", mt: 1.25, ml: 1.5, mr: 0.5, flexShrink: 0 }}>
                    <Checkbox
                      size="small"
                      checked={selection.isSelected(s.id)}
                      onChange={() => selection.toggle(s.id)}
                      aria-label={`Select ${s.name || s.title || "scenario"}`}
                      sx={selectableCheckboxSx}
                    />
                  </Box>
                )}
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <ScenarioDetail row={s} env={env} />
                </Box>
                <Tooltip arrow title={locked ? LOCK_TOOLTIP : SCENARIOS_COPY.editLabel}>
                  <Box component="span" sx={{ display: "inline-flex", mt: 1, flexShrink: 0 }}>
                    <IconButton
                      size="small"
                      disabled={locked}
                      aria-label={SCENARIOS_COPY.editLabel}
                      onClick={() => onEdit?.(s)}
                    >
                      <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
                    </IconButton>
                  </Box>
                </Tooltip>
                <Tooltip arrow title={locked ? LOCK_TOOLTIP : SCENARIOS_COPY.removeLabel}>
                  <Box component="span" sx={{ display: "inline-flex", mt: 1, mr: 1.5, flexShrink: 0 }}>
                    <IconButton
                      size="small"
                      disabled={locked}
                      aria-label={SCENARIOS_COPY.removeLabel}
                      onClick={() => onRemove(s.id)}
                    >
                      <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
                    </IconButton>
                  </Box>
                </Tooltip>
              </Stack>
            ))}
          </Stack>
        </Box>
      )}
    </Box>
  );
}
CollapsibleGroup.propTypes = {
  group: GROUP_SHAPE,
  env: ENV_SHAPE,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onHideGroup: PropTypes.func,
  selection: PropTypes.object,
  locked: PropTypes.bool,
};
