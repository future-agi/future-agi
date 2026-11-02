import PropTypes from "prop-types";
import { lazy, Suspense, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SideDrawer from "../../components/SideDrawer";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import ScenarioSheet from "./detail/ScenarioSheet";
import { MONO } from "./detail/sheet.constants";
import { SCENARIO_SHAPE } from "./scenarios.shapes";

// One scenario, opened from its table row, laid out as Vel's ScenarioDrawer:
// the scenario key with its state and must-pass flag, its source under it,
// Edit and the more-actions menu beside it, then the sheet (figures, the
// scenario, its status, its history). Must-pass and Archive have no API, so
// they are flagged UI only and save nothing; Remove hands the id to the
// parent, which confirms. The latest conversation opens in the run's own call
// drawer.

const uiOnly = () => enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });

// Loaded on the first Open conversation: the call drawers are heavy and most
// visits to a scenario never open its call.
const ConversationDrawer = lazy(() => import("./detail/ConversationDrawer"));

export default function ScenarioDrawer({
  row,
  onClose,
  onEdit,
  onRemove,
  locked = false,
}) {
  return (
    <SideDrawer open={!!row} onClose={onClose} width={680}>
      {row && (
        <DrawerBody
          key={row.id ?? row.name}
          row={row}
          onEdit={onEdit}
          onRemove={onRemove}
          locked={locked}
        />
      )}
    </SideDrawer>
  );
}

ScenarioDrawer.propTypes = {
  row: SCENARIO_SHAPE,
  onClose: PropTypes.func.isRequired,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  locked: PropTypes.bool,
};

function DrawerBody({ row, onEdit, onRemove, locked }) {
  const [mustPass, setMustPass] = useState(!!row.critical);
  const [menuEl, setMenuEl] = useState(null);
  // The call drawer mounts on the first open and stays, so it can animate shut.
  const [call, setCall] = useState({ mounted: false, open: false });
  const callExecutionId = row.callExecutionId || null;
  const closeMenu = () => setMenuEl(null);

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 2,
          pr: 6,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              noWrap
              sx={{
                typography: "m3",
                fontWeight: 600,
                fontFamily: MONO,
                minWidth: 0,
              }}
            >
              {row.name || row.title}
            </Typography>
            <SoonChip
              label="State"
              what="A scenario's state (valid, re-check, outdated, stale or archived)"
              size="tiny"
            />
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.5}
              sx={{ flexShrink: 0 }}
            >
              <MustPassToggle
                on={mustPass}
                locked={locked}
                onToggle={() => {
                  setMustPass((v) => !v);
                  uiOnly();
                }}
              />
              <UiOnlyFlag feature="mustPass" />
            </Stack>
          </Stack>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ mt: 0.5 }}
          >
            <SoonChip
              label="Source"
              what="Where a scenario came from (generated, Error Feed, production, dataset or script)"
              size="tiny"
            />
          </Stack>
        </Box>
        {!locked && onEdit && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => onEdit(row)}
            startIcon={
              <Iconify icon="solar:pen-new-square-linear" width={14} />
            }
            sx={{
              typography: "s2",
              fontWeight: 700,
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            Edit
          </Button>
        )}
        <CustomTooltip
          show
          arrow
          size="small"
          title={locked ? "Fork this environment to edit." : "More actions"}
        >
          <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
            <IconButton
              size="small"
              disabled={locked}
              aria-label="More actions"
              onClick={(e) => setMenuEl(e.currentTarget)}
            >
              <Iconify
                icon="solar:menu-dots-bold"
                width={20}
                sx={{ color: "text.secondary" }}
              />
            </IconButton>
          </Box>
        </CustomTooltip>
        <Menu
          anchorEl={menuEl}
          open={!!menuEl}
          onClose={closeMenu}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { minWidth: 200 } } }}
        >
          {onEdit && (
            <MenuItem
              onClick={() => {
                closeMenu();
                onEdit(row);
              }}
              sx={{ typography: "s2", gap: 1 }}
            >
              <Iconify icon="solar:pen-new-square-linear" width={16} />
              Edit
            </MenuItem>
          )}
          <MenuItem
            onClick={() => {
              closeMenu();
              uiOnly();
            }}
            sx={{ typography: "s2", gap: 1 }}
          >
            <Iconify icon="solar:archive-linear" width={16} />
            Archive
            <UiOnlyFlag feature="scenarioStates" sx={{ ml: 0.5 }} />
          </MenuItem>
          {onRemove && (
            <MenuItem
              onClick={() => {
                closeMenu();
                onRemove(row.id);
              }}
              sx={{ typography: "s2", gap: 1, color: "error.main" }}
            >
              <Iconify icon="solar:trash-bin-trash-linear" width={16} />
              Remove
            </MenuItem>
          )}
        </Menu>
      </Stack>
      <Box sx={{ flex: 1, overflow: "auto" }}>
        <ScenarioSheet
          row={row}
          onOpenConversation={
            callExecutionId
              ? () => setCall({ mounted: true, open: true })
              : undefined
          }
          opening={call.open}
        />
      </Box>
      {call.mounted && callExecutionId && (
        <Suspense fallback={null}>
          <ConversationDrawer
            callExecutionId={callExecutionId}
            open={call.open}
            onClose={() => setCall((c) => ({ ...c, open: false }))}
          />
        </Suspense>
      )}
    </Stack>
  );
}

DrawerBody.propTypes = {
  row: SCENARIO_SHAPE.isRequired,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  locked: PropTypes.bool,
};

function MustPassToggle({ on, locked, onToggle }) {
  return (
    <CustomTooltip
      show
      arrow
      size="small"
      title={
        on
          ? "Must-pass: an improvement that breaks it is rejected."
          : "Not must-pass. Click to mark it."
      }
    >
      <Box component="span" sx={{ display: "inline-flex" }}>
        <IconButton
          size="small"
          aria-label="Must-pass"
          aria-pressed={on}
          disabled={locked}
          onClick={onToggle}
          sx={{ p: 0.25, color: on ? "text.primary" : "text.disabled" }}
        >
          <Iconify
            icon={on ? "solar:shield-check-bold" : "solar:shield-check-linear"}
            width={16}
          />
        </IconButton>
      </Box>
    </CustomTooltip>
  );
}

MustPassToggle.propTypes = {
  on: PropTypes.bool,
  locked: PropTypes.bool,
  onToggle: PropTypes.func,
};
