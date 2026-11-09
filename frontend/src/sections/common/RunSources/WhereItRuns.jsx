// Where it runs (R4-SPEC D5; lane Ra MUST 3): one line `Runs on this
// machine · Change`; `Change` opens one menu over modesFor(kind, entry):
// the primary modes, then `More places to run` with the rest. Unavailable
// modes stay listed, disabled with the reason. No segmented control. ⌘K
// `Where it runs: <mode>` reaches each.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  ListSubheader,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import Term from "src/sections/common/ux/Term";
import { VOCAB } from "src/sections/common/vocab";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { EXECUTION_MODES, catalogEntry, modeLabel, modesFor } from "./catalog";

const SHORT = Object.fromEntries(EXECUTION_MODES.map((m) => [m.id, m.short]));
// SBXCS-FA-RUNSOURCES-01-RL1915: activated withheld copy.
const SANDBOX_WITHHELD = "Future AGI sandbox: Coming soon";

export const MODE_HINT = Object.freeze({
  local: "Your machine runs each row; model keys stay in its environment",
  ci: "A CI job runs the script on push or on demand, then uploads",
  sandbox:
    "Runs in a container we host; your model keys come from workspace secrets",
  remote: "We call your agent's URL for each row",
  platform: "Future AGI runs it",
});

const MODE_ICON = {
  local: "mdi:laptop",
  ci: "mdi:source-branch-check",
  sandbox: "mdi:cube-outline",
  remote: "mdi:web",
  platform: "mdi:cloud-outline",
};

/** What a menu row says about a mode (pure). */
// eslint-disable-next-line no-unused-vars
export function modeHint(modeId, kind, entryOrId) {
  return MODE_HINT[modeId];
}

/** Location copy (pure); sandbox capability defaults to withheld. */
export function whereLine(
  modeId,
  entryOrId,
  // eslint-disable-next-line no-unused-vars
  slot = null,
  { sandboxAllowed = false } = {},
) {
  if (modeId === "sandbox" && sandboxAllowed !== true) {
    return SANDBOX_WITHHELD;
  }
  const entry =
    typeof entryOrId === "string" ? catalogEntry(entryOrId) : entryOrId;
  if (modeId === "remote" && entry?.remoteLabel) {
    const label = entry.remoteLabel;
    // `Runs as a remote endpoint`.
    return `Runs as a ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
  }
  return SHORT[modeId] ?? `Runs: ${modeLabel(modeId, entry)}`;
}

export default function WhereItRuns({
  kind,
  entryId,
  mode,
  onChange,
  machineLabel,
  slot,
}) {
  const [anchor, setAnchor] = useState(null);
  const { allowed, isLoading, isError } = useFeatureAllowed(
    CAPABILITY.OMEGA_SANDBOXES,
  );
  const sandboxAllowed = allowed === true && !isLoading && !isError;
  const entry = catalogEntry(entryId);
  const modes = modesFor(kind, entryId);
  const primary = modes.filter((m) => m.primary);
  const more = modes.filter((m) => !m.primary);
  const pick = (m) => {
    if (m.disabledReason) return;
    setAnchor(null);
    onChange(m.id);
  };
  useRegisterCommands(
    `where-it-runs:${entryId}`,
    modes
      .filter((m) => !m.disabledReason)
      .map((m) => ({
        id: `where.${entryId}.${m.id}`,
        label: `Where it runs: ${modeLabel(m.id, entry)}`,
        keywords: ["where", "mode", "run", m.id],
        icon: MODE_ICON[m.id] ?? "mdi:map-marker-outline",
        run: () => onChange(m.id),
      })),
    [entryId, modes.length],
  );
  const item = (m) => (
    <MenuItem
      key={m.id}
      onClick={() => pick(m)}
      selected={m.id === mode}
      disabled={false}
      aria-disabled={Boolean(m.disabledReason)}
      data-testid={`mode-${m.id}`}
      sx={{
        alignItems: "flex-start",
        gap: 1.25,
        py: 0.75,
        opacity: m.disabledReason ? 0.55 : 1,
        cursor: m.disabledReason ? "not-allowed" : "pointer",
      }}
    >
      <Iconify
        icon={MODE_ICON[m.id] ?? "mdi:map-marker-outline"}
        width={16}
        sx={{ mt: 0.25, color: "text.secondary" }}
      />
      <Box sx={{ minWidth: 0 }}>
        <Stack direction="row" alignItems="baseline" gap={0.75}>
          <Typography typography="s1" fontWeight="fontWeightMedium">
            {m.label}
          </Typography>
        </Stack>
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ whiteSpace: "normal" }}
        >
          {m.disabledReason ?? modeHint(m.id, kind, entry)}
        </Typography>
      </Box>
    </MenuItem>
  );
  const line = whereLine(mode, entry, slot, { sandboxAllowed });
  const onlyOne = modes.filter((m) => !m.disabledReason).length <= 1;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      data-testid="where-it-runs"
      sx={{ minWidth: 0 }}
    >
      <Iconify
        icon={MODE_ICON[mode] ?? "mdi:map-marker-outline"}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Typography typography="s2" noWrap sx={{ minWidth: 0 }}>
        <Term k="executionMode">{line}</Term>
        {machineLabel ? ` · ${machineLabel}` : ""}
      </Typography>
      <Typography typography="s2" color="text.disabled">
        ·
      </Typography>
      <CustomTooltip
        show={onlyOne}
        title={`${entry?.name ?? "This source"} runs in one place only`}
        size="small"
        placement="top"
      >
        <span>
          <Button
            size="small"
            disabled={onlyOne}
            onClick={(e) => setAnchor(e.currentTarget)}
            aria-haspopup="menu"
            data-testid="where-change"
            sx={{
              minWidth: 0,
              px: 0.5,
              typography: "s2",
              whiteSpace: "nowrap",
            }}
          >
            Change where it runs
          </Button>
        </span>
      </CustomTooltip>
      <Menu
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { width: 360 } } }}
        MenuListProps={{ dense: true, "aria-label": VOCAB.WHERE_IT_RUNS }}
      >
        {primary.map(item)}
        {more.length ? <Divider key="d" /> : null}
        {more.length ? (
          <ListSubheader
            key="h"
            disableSticky
            sx={{
              lineHeight: 1.8,
              bgcolor: "transparent",
              typography: "s3",
              color: "text.secondary",
            }}
          >
            {VOCAB.MORE_PLACES}
          </ListSubheader>
        ) : null}
        {more.map(item)}
      </Menu>
    </Stack>
  );
}

WhereItRuns.propTypes = {
  kind: PropTypes.string.isRequired,
  entryId: PropTypes.string,
  mode: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  machineLabel: PropTypes.string,
  slot: PropTypes.object,
};
