import React, { useState } from "react";
import PropTypes from "prop-types";
import { useParams } from "react-router";
import {
  Box,
  ButtonBase,
  Checkbox,
  Divider,
  FormControlLabel,
  ListItemButton,
  ListItemText,
  MenuItem,
  Popover,
  Tab,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";

const GROUP_OPTIONS = [
  { key: "trace", label: "Trace" },
  { key: "span", label: "Span" },
  { key: "users", label: "Users" },
  { key: "sessions", label: "Sessions" },
];

const ROW_HEIGHT_OPTIONS = ["Short", "Medium", "Large", "Extra Large"];

const SectionHeader = ({ children }) => (
  <Typography
    variant="overline"
    sx={{
      fontSize: 12,
      fontWeight: 600,
      color: "text.disabled",
      letterSpacing: 0,
      px: 2,
      pt: 1.5,
      pb: 0.5,
      display: "block",
    }}
  >
    {children}
  </Typography>
);

SectionHeader.propTypes = { children: PropTypes.node };

// `icon` is intentionally ignored — the row-level icons were removed
// from the Display panel because they added visual noise without extra
// information. Kept in the prop signature so existing call sites don't
// need to change.
const PanelRow = ({ label, value, onClick, hasMenu = false }) => (
  <ListItemButton onClick={onClick} dense sx={{ px: 2, py: 0.5 }}>
    <ListItemText
      primary={label}
      primaryTypographyProps={{ variant: "body2", fontSize: 13 }}
    />
    {value && (
      <Typography variant="body2" sx={{ fontSize: 12, color: "text.disabled" }}>
        {value}
      </Typography>
    )}
    {hasMenu && (
      <Iconify
        icon="solar:alt-arrow-right-linear"
        width={16}
        color="text.disabled"
        sx={{ ml: 0.5 }}
      />
    )}
  </ListItemButton>
);

PanelRow.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string.isRequired,
  value: PropTypes.string,
  onClick: PropTypes.func,
  hasMenu: PropTypes.bool,
};

// `icon` is intentionally ignored — see PanelRow comment above.
const PanelCheck = ({ label, checked, onChange }) => (
  <Box sx={{ px: 2, py: 0.25 }}>
    <FormControlLabel
      control={
        <Checkbox
          size="small"
          checked={checked}
          onChange={onChange}
          sx={{ p: 0.5 }}
        />
      }
      label={
        <Typography variant="body2" sx={{ fontSize: 13 }}>
          {label}
        </Typography>
      }
      sx={{ ml: 0 }}
    />
  </Box>
);

PanelCheck.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string.isRequired,
  checked: PropTypes.bool,
  onChange: PropTypes.func,
};

// ---------------------------------------------------------------------------
// View mode tab button (Graph View / Agent Graph / Agent Path)
// ---------------------------------------------------------------------------
const VIEW_MODES = [
  { key: "graph", label: "Graph View", icon: "solar:graph-up-linear" },
  { key: "agentGraph", label: "Agent Graph", icon: "solar:graph-linear" },
  {
    key: "agentPath",
    label: "Agent Path",
    icon: "solar:routing-2-linear",
  },
];

// ---------------------------------------------------------------------------
// DisplayPanel
// ---------------------------------------------------------------------------
const DisplayPanel = ({
  anchorEl,
  open,
  onClose,
  // Mode: "traces" (default) | "sessions" | "users"
  mode = "traces",
  // View mode
  viewMode,
  onViewModeChange,
  agentGraphEnabled = true,
  // Rows
  cellHeight,
  setCellHeight,
  // Columns
  columns,
  onColumnVisibilityChange,
  onAutoSize,
  autoSizeAllCols,
  onAddCustomColumn,
  // Metrics
  hasEvalFilter,
  onToggleEvalFilter,
  showEvalToggle,
  // Metrics
  showErrors,
  onToggleErrors,
  showNonAnnotated,
  onToggleNonAnnotated,
  // Group
  groupBy,
  onGroupByChange,
  hiddenGroupByOptions = [],
  // Graph
  onCompareToggle,
  isCompareActive,
  onResetView,
  onSetDefaultView,
  // Voice / Simulator
  isSimulator,
  excludeSimulationCalls,
  onToggleSimulationCalls,
}) => {
  const isTraces = mode === "traces";
  const isUsers = mode === "users";
  const isSessions = mode === "sessions";
  // Standalone users page: /dashboard/users (no observeId in params).
  // Users tab inside observe: /dashboard/observe/:observeId/users — keep
  // Group + Graph visible there so users can switch groupings and compare.
  const { observeId } = useParams();
  const isStandaloneUsersPage = isUsers && !observeId;
  const hiddenCount = columns?.filter((c) => !c.isVisible)?.length || 0;
  // Dedupe by id so the count matches the dialog's checked-set size
  // (which is keyed by id). Without this, any duplicate custom-column
  // entry in state would show "N+1 added" relative to what the dialog
  // can manage. See TH-4139.
  const customColumnCount = new Set(
    (columns || [])
      .filter((c) => c.groupBy === "Custom Columns")
      .map((c) => c.id),
  ).size;
  const [groupAnchor, setGroupAnchor] = useState(null);
  const [rowHeightAnchor, setRowHeightAnchor] = useState(null);
  const activeGroup = GROUP_OPTIONS.find((o) => o.key === groupBy);

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{
        paper: {
          sx: { width: 320, maxHeight: "calc(100vh - 120px)", mt: 0.5 },
        },
      }}
    >
      {/* View mode tabs — traces only */}
      {isTraces && (
        <>
          <Box sx={{ px: 1.5, pt: 1, pb: 0.5 }}>
            <SegmentedTabs
              value={viewMode}
              onChange={(_, key) => onViewModeChange?.(key)}
              sx={{ width: "100%", "& .MuiTab-root": { flex: 1 } }}
            >
              {VIEW_MODES.map((vm) => {
                const isDisabled =
                  vm.key !== "graph" && (isSimulator || !agentGraphEnabled);
                return (
                  <Tab
                    key={vm.key}
                    value={vm.key}
                    icon={<Iconify icon={vm.icon} width={14} />}
                    iconPosition="start"
                    label={vm.label}
                    disabled={isDisabled}
                    title={
                      isDisabled
                        ? isSimulator
                          ? "Not available for voice projects"
                          : "Select a project filter to use agent visualizations"
                        : undefined
                    }
                  />
                );
              })}
            </SegmentedTabs>
          </Box>
          <Divider sx={{ my: 0.5 }} />
        </>
      )}

      {/* ROWS section */}
      <SectionHeader>ROWS</SectionHeader>
      <PanelRow
        icon="solar:branching-paths-up-linear"
        label="Row height"
        value={cellHeight || "Short"}
        hasMenu
        onClick={(e) => setRowHeightAnchor(e.currentTarget)}
      />
      <Popover
        open={Boolean(rowHeightAnchor)}
        anchorEl={rowHeightAnchor}
        onClose={() => setRowHeightAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { minWidth: 200, ml: 0.5 } } }}
      >
        {ROW_HEIGHT_OPTIONS.map((opt) => {
          const isSelected = opt === (cellHeight || "Short");
          return (
            <MenuItem
              key={opt}
              selected={isSelected}
              onClick={() => {
                setRowHeightAnchor(null);
                setCellHeight?.(opt);
              }}
              sx={{
                py: 0.75,
                display: "flex",
                alignItems: "center",
                gap: 1,
              }}
            >
              {isSelected ? (
                <Iconify icon="solar:check-linear" width={14} />
              ) : (
                <Box sx={{ width: 14 }} />
              )}
              {opt}
            </MenuItem>
          );
        })}
      </Popover>

      <Divider sx={{ my: 0.5 }} />

      {/* COLUMNS section */}
      <SectionHeader>COLUMNS</SectionHeader>
      <PanelRow
        icon="solar:eye-linear"
        label="View columns"
        value={hiddenCount > 0 ? `${hiddenCount} hidden` : undefined}
        hasMenu
        onClick={(e) => {
          onColumnVisibilityChange?.(e);
        }}
      />
      <PanelRow
        icon="mdi:arrow-expand-horizontal"
        label={autoSizeAllCols ? "Reset columns" : "Autosize columns"}
        onClick={onAutoSize}
      />
      <PanelRow
        icon="solar:add-linear"
        label="Add custom columns"
        value={customColumnCount > 0 ? `${customColumnCount} added` : undefined}
        hasMenu
        onClick={onAddCustomColumn}
      />

      {/* METRICS — trace-specific checkboxes; hidden on users and sessions
       * (sessions view has no consumer for these filters). */}
      {!isUsers && !isSessions && (
        <>
          <Divider sx={{ my: 0.5 }} />

          <SectionHeader>METRICS</SectionHeader>
          {isSimulator && (
            <PanelCheck
              icon="solar:phone-linear"
              label="Show simulated calls"
              checked={!excludeSimulationCalls}
              onChange={onToggleSimulationCalls}
            />
          )}
          {showEvalToggle && (
            <PanelCheck
              icon="solar:check-circle-linear"
              label="Show traces with evals"
              checked={hasEvalFilter}
              onChange={onToggleEvalFilter}
            />
          )}
          <PanelCheck
            icon="solar:danger-circle-linear"
            label="Errors"
            checked={!!showErrors}
            onChange={onToggleErrors}
          />
          <PanelCheck
            icon="solar:chat-round-linear"
            label="Non annotated"
            checked={!!showNonAnnotated}
            onChange={onToggleNonAnnotated}
          />
        </>
      )}

      {/* GROUP + GRAPH — hidden only on the standalone users page; kept
       * on the users tab inside observe since the user can still group
       * by trace/span/sessions and toggle compare from there. */}
      {!isStandaloneUsersPage && (
        <>
          <Divider sx={{ my: 0.5 }} />

          {/* GROUP section */}
          <SectionHeader>GROUP</SectionHeader>
          <PanelRow
            icon="solar:users-group-rounded-linear"
            label="Group traces by"
            value={activeGroup?.label}
            hasMenu
            onClick={(e) => setGroupAnchor(e.currentTarget)}
          />
          <Popover
            open={Boolean(groupAnchor)}
            anchorEl={groupAnchor}
            onClose={() => setGroupAnchor(null)}
            anchorOrigin={{ vertical: "top", horizontal: "right" }}
            transformOrigin={{ vertical: "top", horizontal: "left" }}
            slotProps={{ paper: { sx: { minWidth: 200, ml: 0.5 } } }}
          >
            {GROUP_OPTIONS.filter(
              (opt) => !hiddenGroupByOptions.includes(opt.key),
            ).map((opt) => {
              const isSelected = opt.key === groupBy;
              return (
                <MenuItem
                  key={opt.key}
                  selected={isSelected}
                  onClick={() => {
                    setGroupAnchor(null);
                    onGroupByChange?.(opt.key);
                  }}
                  sx={{
                    py: 0.75,
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                  }}
                >
                  {isSelected ? (
                    <Iconify icon="solar:check-linear" width={14} />
                  ) : (
                    <Box sx={{ width: 14 }} />
                  )}
                  {opt.label}
                </MenuItem>
              );
            })}
          </Popover>

          <Divider sx={{ my: 0.5 }} />

          {/* GRAPH section */}
          <SectionHeader>GRAPH</SectionHeader>
          <PanelCheck
            icon="solar:graph-up-linear"
            label="Compare graph"
            checked={isCompareActive}
            onChange={onCompareToggle}
          />
        </>
      )}

      <Divider sx={{ my: 0.5 }} />

      {/* SETTINGS */}
      <SectionHeader>SETTINGS</SectionHeader>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 2,
          pt: 0.5,
          pb: 1,
        }}
      >
        <ButtonBase
          onClick={() => {
            onResetView?.();
            onClose();
          }}
          sx={{
            fontSize: 13,
            fontFamily: "'IBM Plex Sans', sans-serif",
            color: "text.primary",
            py: 0.5,
          }}
        >
          Reset
        </ButtonBase>
        <ButtonBase
          onClick={() => {
            onSetDefaultView?.();
            onClose();
          }}
          sx={{
            fontSize: 13,
            fontFamily: "'IBM Plex Sans', sans-serif",
            color: "text.link",
            py: 0.5,
          }}
        >
          Set default for everyone
        </ButtonBase>
      </Box>
    </Popover>
  );
};

DisplayPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  mode: PropTypes.oneOf(["traces", "sessions", "users"]),
  viewMode: PropTypes.string,
  onViewModeChange: PropTypes.func,
  agentGraphEnabled: PropTypes.bool,
  cellHeight: PropTypes.string,
  setCellHeight: PropTypes.func,
  columns: PropTypes.array,
  onColumnVisibilityChange: PropTypes.func,
  onAutoSize: PropTypes.func,
  autoSizeAllCols: PropTypes.bool,
  onAddCustomColumn: PropTypes.func,
  hasEvalFilter: PropTypes.bool,
  onToggleEvalFilter: PropTypes.func,
  showEvalToggle: PropTypes.bool,
  showErrors: PropTypes.bool,
  onToggleErrors: PropTypes.func,
  showNonAnnotated: PropTypes.bool,
  onToggleNonAnnotated: PropTypes.func,
  groupBy: PropTypes.string,
  onGroupByChange: PropTypes.func,
  hiddenGroupByOptions: PropTypes.arrayOf(PropTypes.string),
  onCompareToggle: PropTypes.func,
  isCompareActive: PropTypes.bool,
  onResetView: PropTypes.func,
  onSetDefaultView: PropTypes.func,
  isSimulator: PropTypes.bool,
  excludeSimulationCalls: PropTypes.bool,
  onToggleSimulationCalls: PropTypes.func,
};

export default React.memo(DisplayPanel);
