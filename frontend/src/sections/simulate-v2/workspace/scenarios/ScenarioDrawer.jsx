import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Divider,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import ScenarioDetail from "../../components/ScenarioDetail";
import { stateActionOf, STATE_ACTION_LABEL } from "../../_mock/scenarioStates";
import { ArchivePopover, StateChip } from "./ScenarioStateBits";
import BlockerFlag from "./BlockerFlag";
import SourceBadge from "./SourceBadge";
import TraceSources from "./TraceSources";
import SourceChangedNotice from "./SourceChangedNotice";
import { scenarioTitle } from "../../_mock/scenarios";

/*
  One scenario, opened from the table: the same detail the list view expands
  into, read as a sheet in four parts — the scenario, its recent runs, its
  status (the two questions and the proof) and its history (version, where
  it came from). The table is the default view, so without this the
  state's "why" and its actions were a view-switch away.
*/
export default function ScenarioDrawer({
  row,
  env,
  envState,
  state,
  onClose,
  onAction,
  onEdit,
  locked = false,
}) {
  const [menuEl, setMenuEl] = useState(null);
  const [archiveEl, setArchiveEl] = useState(null);
  const can = !!onAction && !locked && !!row;
  /* The one thing the state asks for (Re-prove, Rewrite, Update, Restore) is
     a button; Add (for a proposed scenario) and Archive sit in the menu. */
  const primary = can && state ? stateActionOf(state) : null;
  const archived = state?.id === "archived" || !!row?.archived;
  const addMove =
    can && !archived && row.proposed
      ? {
          kind: "approve",
          label: "Add to the environment",
          icon: "solar:check-circle-linear",
        }
      : null;
  const menuItems = [
    addMove,
    can && !archived
      ? { kind: "archive", label: "Archive…", icon: "solar:archive-linear" }
      : null,
  ].filter(Boolean);
  return (
    <SideDrawer open={!!row} onClose={onClose} width={640}>
      {row && (
        <Stack sx={{ height: "100%" }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              px: 2.5,
              py: 2,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            {/* The readable line leads; the slug is its id, quietly under it. */}
            <Box flex={1} minWidth={0}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                className="blocker-host"
              >
                <Typography noWrap sx={{ typography: "m2", fontWeight: 600 }}>
                  {row.summary || scenarioTitle(row.title) || row.name}
                </Typography>
                {state && <StateChip state={state} />}
                {/* Must-pass, toggled here as in the table — locked like every other edit. */}
                <BlockerFlag
                  row={row}
                  size={15}
                  locked={locked || !!row.archived}
                  onToggle={
                    onAction
                      ? () => onAction("blocker", row, !row.critical)
                      : undefined
                  }
                />
              </Stack>
              {/* Its id, and where it came from — the rows leave the source out. */}
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mt: 0.375, minWidth: 0 }}
              >
                {row.name && (row.summary || row.title) && (
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      fontFamily: "ui-monospace, Menlo, monospace",
                      minWidth: 0,
                    }}
                  >
                    {row.name}
                  </Typography>
                )}
                <SourceBadge
                  scenario={row}
                  env={env}
                  envState={envState}
                  labelled
                />
              </Stack>
            </Box>
            {primary && (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => onAction(primary, row)}
                sx={{ flexShrink: 0 }}
              >
                {STATE_ACTION_LABEL[primary]}
              </Button>
            )}
            {!locked && onEdit && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => onEdit(row)}
                startIcon={
                  <Iconify icon="solar:pen-new-square-linear" width={14} />
                }
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  flexShrink: 0,
                }}
              >
                Edit
              </Button>
            )}
            {menuItems.length > 0 && (
              <Tooltip arrow title="More actions">
                <IconButton
                  size="small"
                  onClick={(e) => setMenuEl(e.currentTarget)}
                  aria-label="More actions"
                >
                  <Iconify
                    icon="solar:menu-dots-bold"
                    width={20}
                    sx={{ color: "text.secondary" }}
                  />
                </IconButton>
              </Tooltip>
            )}
            <Menu
              anchorEl={menuEl}
              open={!!menuEl}
              onClose={() => setMenuEl(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "right" }}
              slotProps={{ paper: { sx: { minWidth: 240 } } }}
            >
              {menuItems.map((m, i) => [
                i > 0 && m.kind === "archive" && (
                  <Divider key={`d-${m.kind}`} />
                ),
                <MenuItem
                  key={m.kind}
                  onClick={() => {
                    const anchorEl = menuEl;
                    setMenuEl(null);
                    if (m.kind === "archive") setArchiveEl(anchorEl);
                    else onAction(m.kind, row);
                  }}
                  sx={{ typography: "s2" }}
                >
                  <ListItemIcon>
                    <Iconify icon={m.icon} width={16} />
                  </ListItemIcon>
                  {m.label}
                </MenuItem>,
              ])}
            </Menu>
            {row && (
              <ArchivePopover
                anchorEl={archiveEl}
                onClose={() => setArchiveEl(null)}
                onArchive={(note) => onAction("archive", row, note)}
              />
            )}
            <IconButton size="small" onClick={onClose} aria-label="Close">
              <Iconify
                icon="solar:close-linear"
                width={20}
                sx={{ color: "text.secondary" }}
              />
            </IconButton>
          </Stack>
          <Box sx={{ flex: 1, overflow: "auto" }}>
            {/* Grounded in a file that has a newer version waiting. */}
            {row.citedSource && (
              <SourceChangedNotice row={row} env={env} locked={locked} />
            )}
            <ScenarioDetail
              bare
              row={row}
              env={env}
              envState={envState}
              state={state}
              onAction={onAction}
              locked={locked}
            />
            {/* Made from production traces: its sources and the chain to now. */}
            {row.traceImport && (
              <TraceSources row={row} env={env} envState={envState} />
            )}
          </Box>
        </Stack>
      )}
    </SideDrawer>
  );
}
ScenarioDrawer.propTypes = {
  row: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  state: PropTypes.object,
  onClose: PropTypes.func,
  onAction: PropTypes.func,
  onEdit: PropTypes.func,
  locked: PropTypes.bool,
};
