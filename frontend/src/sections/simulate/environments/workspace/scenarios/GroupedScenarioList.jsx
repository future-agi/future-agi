import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, IconButton, Tooltip } from "@mui/material";

import Iconify from "src/components/iconify";
import ScenarioDetail from "./ScenarioDetail";
import { SCENARIOS_COPY } from "./scenarios.constants";
import { ENV_SHAPE, GROUP_SHAPE } from "./scenarios.shapes";

const onKeyActivate = (fn) => (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fn();
  }
};

// The grouped body: each use-case section as a collapsible block with a sticky
// header. The per-row edit pencil opens the editor through onEdit; remove works
// through onRemove.
export default function GroupedScenarioList({ groups, env, onEdit, onRemove }) {
  return (
    <Box>
      {groups.map((g) => (
        <CollapsibleGroup key={g.id} group={g} env={env} onEdit={onEdit} onRemove={onRemove} />
      ))}
    </Box>
  );
}
GroupedScenarioList.propTypes = {
  groups: PropTypes.arrayOf(GROUP_SHAPE),
  env: ENV_SHAPE,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
};

function CollapsibleGroup({ group, env, onEdit, onRemove }) {
  const [open, setOpen] = useState(true);

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
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <ScenarioDetail row={s} env={env} />
                </Box>
                <Tooltip arrow title={SCENARIOS_COPY.editLabel}>
                  <IconButton
                    size="small"
                    aria-label={SCENARIOS_COPY.editLabel}
                    onClick={() => onEdit?.(s)}
                    sx={{ mt: 1, flexShrink: 0 }}
                  >
                    <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Tooltip>
                <Tooltip arrow title={SCENARIOS_COPY.removeLabel}>
                  <IconButton
                    size="small"
                    aria-label={SCENARIOS_COPY.removeLabel}
                    onClick={() => onRemove(s.id)}
                    sx={{ mt: 1, mr: 1.5, flexShrink: 0 }}
                  >
                    <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
                  </IconButton>
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
};
