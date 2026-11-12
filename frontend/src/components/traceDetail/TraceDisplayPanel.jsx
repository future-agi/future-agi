import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  Checkbox,
  Divider,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Popover,
  Switch,
  Tab,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { getTypeConfig } from "./spanTypeConfig";
import { SpanTypes } from "src/utils/constant";

// ---------------------------------------------------------------------------
// Default config — used for "Reset view"
// ---------------------------------------------------------------------------
export const DEFAULT_VIEW_CONFIG = {
  viewMode: "tree",
  spanTypeFilter: null,
  visibleMetrics: {
    latency: true,
    tokens: true,
    cost: false,
    evals: false,
    annotations: false,
    events: false,
  },
  showAgentGraph: true,
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------
const SectionHeader = ({ children }) => (
  <Typography
    sx={{
      fontSize: 12,
      fontWeight: 400,
      color: "text.secondary",
      letterSpacing: 0,
      px: 0.5,
      pt: 1,
      pb: 0.25,
      display: "block",
      fontFamily: "'IBM Plex Sans', sans-serif",
    }}
  >
    {children}
  </Typography>
);

SectionHeader.propTypes = { children: PropTypes.node };

const MetricToggle = ({ label, checked, onChange }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      px: 0.5,
      py: 0.25,
      borderRadius: "4px",
      "&:hover": { bgcolor: "action.hover" },
    }}
  >
    <Typography
      sx={{
        fontSize: 13,
        fontFamily: "'IBM Plex Sans', sans-serif",
        color: "text.primary",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </Typography>
    <Switch
      size="small"
      checked={checked}
      onChange={onChange}
      color="primary"
    />
  </Box>
);

MetricToggle.propTypes = {
  label: PropTypes.string.isRequired,
  checked: PropTypes.bool,
  onChange: PropTypes.func,
};

// ---------------------------------------------------------------------------
// View mode tabs — large pills with icon on top + label below
// ---------------------------------------------------------------------------
const VIEW_MODES = [
  { key: "tree", label: "Trace View", icon: "solar:graph-linear" },
  {
    key: "timeline",
    label: "Timeline View",
    icon: "solar:clock-circle-linear",
  },
];

// ---------------------------------------------------------------------------
// Span type options
// ---------------------------------------------------------------------------

const SPAN_TYPES = SpanTypes.map((s) => ({ key: s.value, label: s.label }));

// ---------------------------------------------------------------------------
// TraceDisplayPanel
// ---------------------------------------------------------------------------
const TraceDisplayPanel = ({
  anchorEl,
  open,
  onClose,
  viewMode,
  onViewModeChange,
  spanTypeFilter,
  onSpanTypeFilterChange,
  visibleMetrics,
  onToggleMetric,
  showAgentGraph,
  onToggleAgentGraph,
  onResetView,
  onSetDefaultView,
  hideSetDefault = false,
}) => {
  const [spanTypeAnchor, setSpanTypeAnchor] = useState(null);

  // Span type label
  const activeTypes = spanTypeFilter || SPAN_TYPES.map((t) => t.key);
  const allSelected =
    !spanTypeFilter || activeTypes.length === SPAN_TYPES.length;
  const spanTypeLabel = allSelected
    ? "All types"
    : activeTypes
        .map((t) => {
          const found = SPAN_TYPES.find((st) => st.key === t);
          return found ? found.label : t;
        })
        .join(", ");
  const displayLabel =
    spanTypeLabel.length > 22
      ? spanTypeLabel.slice(0, 20) + ".."
      : spanTypeLabel;

  const handleToggleSpanType = (typeKey) => {
    const current = spanTypeFilter || SPAN_TYPES.map((t) => t.key);
    const isActive = current.includes(typeKey);
    let next;
    if (isActive) {
      next = current.filter((t) => t !== typeKey);
      if (next.length === 0) next = SPAN_TYPES.map((t) => t.key); // can't deselect all
    } else {
      next = [...current, typeKey];
    }
    // If all selected, pass null (no filter)
    onSpanTypeFilterChange?.(next.length === SPAN_TYPES.length ? null : next);
  };

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{
        paper: {
          sx: {
            width: 320,
            maxHeight: 560,
            mt: 0.5,
            p: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
          },
        },
      }}
    >
      {/* View mode tabs */}
      <SegmentedTabs
        value={viewMode}
        onChange={(_, key) => onViewModeChange?.(key)}
        sx={{ width: "100%", "& .MuiTab-root": { flex: 1 } }}
      >
        {VIEW_MODES.map((mode) => (
          <Tab
            key={mode.key}
            value={mode.key}
            icon={<Iconify icon={mode.icon} width={16} />}
            iconPosition="start"
            label={mode.label}
          />
        ))}
      </SegmentedTabs>

      {/* TRACE VIEW section */}
      <SectionHeader>Trace view</SectionHeader>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 1,
          py: 0.25,
          borderRadius: "4px",
        }}
      >
        <Typography
          sx={{
            fontSize: 13,
            fontFamily: "'IBM Plex Sans', sans-serif",
            color: "text.primary",
          }}
        >
          Span Type
        </Typography>
        <ButtonBase
          onClick={(e) => setSpanTypeAnchor(e.currentTarget)}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            px: 1,
            py: 0.5,
            bgcolor: "background.paper",
            maxWidth: 190,
          }}
        >
          <Typography
            noWrap
            sx={{
              fontSize: 13,
              fontFamily: "'IBM Plex Sans', sans-serif",
              color: "text.primary",
              flex: 1,
              textAlign: "left",
            }}
          >
            {displayLabel}
          </Typography>
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={16}
            color="text.secondary"
          />
        </ButtonBase>
      </Box>

      {/* Span type dropdown */}
      <Menu
        anchorEl={spanTypeAnchor}
        open={Boolean(spanTypeAnchor)}
        onClose={() => setSpanTypeAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 200, mt: 0.5 } } }}
      >
        {SPAN_TYPES.map((st) => {
          const cfg = getTypeConfig(st.key);
          return (
            <MenuItem
              key={st.key}
              dense
              onClick={() => handleToggleSpanType(st.key)}
              sx={{ py: 0.5, gap: 0.75 }}
            >
              <Checkbox
                size="small"
                checked={activeTypes.includes(st.key)}
                sx={{ p: 0.25 }}
              />
              <Iconify
                icon={cfg.mdiIcon}
                width={16}
                sx={{ color: cfg.color, flexShrink: 0 }}
              />
              <Typography sx={{ fontSize: 13 }}>{st.label}</Typography>
            </MenuItem>
          );
        })}
      </Menu>

      <Divider sx={{ my: 0.75 }} />

      {/* SPAN METRICS section */}
      <SectionHeader>Span metrics</SectionHeader>
      <MetricToggle
        label="Latency"
        checked={visibleMetrics?.latency ?? true}
        onChange={() => onToggleMetric?.("latency")}
      />
      <MetricToggle
        label="Tokens"
        checked={visibleMetrics?.tokens ?? true}
        onChange={() => onToggleMetric?.("tokens")}
      />
      <MetricToggle
        label="Cost"
        checked={visibleMetrics?.cost ?? false}
        onChange={() => onToggleMetric?.("cost")}
      />
      <MetricToggle
        label="Evals"
        checked={visibleMetrics?.evals ?? false}
        onChange={() => onToggleMetric?.("evals")}
      />
      <MetricToggle
        label="Annotations"
        checked={visibleMetrics?.annotations ?? false}
        onChange={() => onToggleMetric?.("annotations")}
      />
      <MetricToggle
        label="Events"
        checked={visibleMetrics?.events ?? false}
        onChange={() => onToggleMetric?.("events")}
      />

      <Divider sx={{ my: 0.75 }} />

      {/* AGENT GRAPH section */}
      <SectionHeader>Agent graph</SectionHeader>
      <MetricToggle
        label="Show agent graph"
        checked={showAgentGraph ?? true}
        onChange={() => onToggleAgentGraph?.()}
      />

      <Divider sx={{ my: 0.75 }} />

      {/* SETTINGS section */}
      <SectionHeader>SETTINGS</SectionHeader>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 0.5,
          pt: 0.5,
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
        {!hideSetDefault && (
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
        )}
      </Box>
      <Box sx={{ pb: 0.5 }} />
    </Popover>
  );
};

TraceDisplayPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  viewMode: PropTypes.string,
  onViewModeChange: PropTypes.func,
  spanTypeFilter: PropTypes.array,
  onSpanTypeFilterChange: PropTypes.func,
  visibleMetrics: PropTypes.object,
  onToggleMetric: PropTypes.func,
  showAgentGraph: PropTypes.bool,
  onToggleAgentGraph: PropTypes.func,
  onResetView: PropTypes.func,
  onSetDefaultView: PropTypes.func,
  hideSetDefault: PropTypes.bool,
};

export default React.memo(TraceDisplayPanel);
