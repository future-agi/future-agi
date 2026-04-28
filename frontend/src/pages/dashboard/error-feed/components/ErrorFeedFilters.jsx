import React, { useMemo, useState } from "react";
import {
  Button,
  Chip,
  Divider,
  IconButton,
  InputAdornment,
  Menu,
  MenuItem,
  OutlinedInput,
  Select,
  Stack,
  Tooltip,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useObserveProjectList,
  useUpdateErrorFeedIssue,
} from "src/api/errorFeed/error-feed";
import { useErrorFeedStore } from "../store";
import PropTypes from "prop-types";

const STATUS_OPTIONS = [
  { value: "", label: "All Statuses" },
  { value: "escalating", label: "Escalating" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "for_review", label: "For review" },
  { value: "resolved", label: "Resolved" },
];

const SEVERITY_OPTIONS = [
  { value: "", label: "All Severities" },
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

const TIME_RANGE_OPTIONS = [
  { value: "1", label: "Last 24 hours" },
  { value: "7", label: "Last 7 days" },
  { value: "14", label: "Last 14 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
];

const FIX_LAYER_OPTIONS = [
  { value: "", label: "All Fix Layers" },
  { value: "Prompt", label: "Prompt" },
  { value: "Tools", label: "Tools" },
  { value: "Orchestration", label: "Orchestration" },
  { value: "Guardrails", label: "Guardrails" },
];

// ── compact select ─────────────────────────────────────────────────────────
function CompactSelect({ value, onChange, options, minWidth = 130 }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  return (
    <Select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      size="small"
      displayEmpty
      SelectDisplayProps={{
        style: {
          fontSize: "13px",
          lineHeight: "32px",
          padding: "0 28px 0 10px",
          minHeight: "unset",
        },
      }}
      sx={{
        height: 32,
        fontSize: "13px",
        minWidth,
        borderRadius: "6px",
        "& .MuiOutlinedInput-notchedOutline": { borderColor: "divider" },
        "&:hover .MuiOutlinedInput-notchedOutline": {
          borderColor: isDark ? "rgba(255,255,255,0.4)" : "rgba(0,0,0,0.4)",
        },
        "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
          borderColor: "primary.main",
          borderWidth: "1px",
        },
        "& .MuiSelect-select": {
          fontSize: "13px !important",
          padding: "0 28px 0 10px !important",
          lineHeight: "32px",
        },
        "& .MuiSelect-icon": { right: 6 },
      }}
      MenuProps={{
        PaperProps: {
          elevation: 3,
          sx: {
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            mt: 0.5,
            "& .MuiMenuItem-root": {
              fontSize: "13px",
              minHeight: 32,
              py: 0.75,
            },
          },
        },
      }}
    >
      {options.map((opt) => (
        <MenuItem key={opt.value} value={opt.value}>
          {opt.label}
        </MenuItem>
      ))}
    </Select>
  );
}
CompactSelect.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  options: PropTypes.array,
  minWidth: PropTypes.number,
};

// ── bulk action menu ───────────────────────────────────────────────────────
function BulkActionMenu({ count, selected, onClear }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const updateIssue = useUpdateErrorFeedIssue();

  const actions = [
    { label: "Mark as Resolved", icon: "mdi:check-circle", status: "resolved" },
    {
      label: "Mark as Acknowledged",
      icon: "mdi:check-circle-outline",
      status: "acknowledged",
    },
    {
      label: "Mark as For Review",
      icon: "mdi:eye-outline",
      status: "for_review",
    },
    {
      label: "Mark as Escalating",
      icon: "mdi:trending-up",
      status: "escalating",
    },
  ];

  const handleBulkStatus = (status) => {
    selected.forEach((clusterId) => {
      updateIssue.mutate({ clusterId, status });
    });
    setAnchorEl(null);
    onClear();
  };

  if (count === 0) return null;

  return (
    <>
      <Chip
        label={`${count} selected`}
        size="small"
        sx={{
          height: 28,
          fontSize: "12px",
          fontWeight: 600,
          bgcolor: "primary.main",
          color: "white",
          borderRadius: "4px",
        }}
        deleteIcon={<Iconify icon="mdi:close" width={14} />}
        onDelete={onClear}
      />
      <Button
        size="small"
        variant="outlined"
        endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        onClick={(e) => setAnchorEl(e.currentTarget)}
        sx={{ height: 32, fontSize: "13px", borderRadius: "6px" }}
      >
        Bulk actions
      </Button>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={() => setAnchorEl(null)}
        PaperProps={{
          elevation: 3,
          sx: {
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            minWidth: 180,
          },
        }}
      >
        {actions.map((a) => (
          <MenuItem
            key={a.status}
            onClick={() => handleBulkStatus(a.status)}
            sx={{ fontSize: "13px", gap: 1 }}
          >
            <Iconify
              icon={a.icon}
              width={16}
              sx={{ color: "text.secondary" }}
            />
            {a.label}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
BulkActionMenu.propTypes = {
  count: PropTypes.number,
  selected: PropTypes.array,
  onClear: PropTypes.func,
};

// ── main filter bar ────────────────────────────────────────────────────────
export default function ErrorFeedFilters({ selected, onClearSelection }) {
  const {
    searchQuery,
    setSearchQuery,
    selectedProject,
    setSelectedProject,

    selectedStatus,
    setSelectedStatus,
    selectedSeverity,
    setSelectedSeverity,
    selectedFixLayer,
    setSelectedFixLayer,
    timeRange,
    setTimeRange,
  } = useErrorFeedStore();

  const { data: projects } = useObserveProjectList();
  const projectOptions = useMemo(
    () => [{ value: "", label: "All Projects" }, ...(projects ?? [])],
    [projects],
  );

  const hasActiveFilters =
    selectedProject || selectedStatus || selectedSeverity || selectedFixLayer;

  const clearAllFilters = () => {
    setSelectedProject("");
    setSelectedStatus("");
    setSelectedSeverity("");
    setSelectedFixLayer("");
    setSearchQuery("");
  };

  return (
    <Stack gap={1.25}>
      {/* Row 1: search + quick filters */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1.5}
      >
        <Stack direction="row" alignItems="center" gap={1}>
          {/* Search */}
          <OutlinedInput
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search errors"
            size="small"
            startAdornment={
              <InputAdornment position="start">
                <Iconify
                  icon="mdi:magnify"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            }
            endAdornment={
              searchQuery ? (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    onClick={() => setSearchQuery("")}
                    edge="end"
                  >
                    <Iconify icon="mdi:close" width={14} />
                  </IconButton>
                </InputAdornment>
              ) : null
            }
            sx={{
              height: 32,
              width: 280,
              fontSize: "13px",
              borderRadius: "6px",
              "& .MuiOutlinedInput-notchedOutline": { borderColor: "divider" },
            }}
            notched={false}
          />

          <Divider
            orientation="vertical"
            flexItem
            sx={{ mx: 0.25, height: 20, alignSelf: "center" }}
          />

          {/* Quick selects */}
          <CompactSelect
            value={timeRange}
            onChange={setTimeRange}
            options={TIME_RANGE_OPTIONS}
            minWidth={140}
          />
          <CompactSelect
            value={selectedProject}
            onChange={setSelectedProject}
            options={projectOptions}
            minWidth={140}
          />

          <CompactSelect
            value={selectedStatus}
            onChange={setSelectedStatus}
            options={STATUS_OPTIONS}
            minWidth={125}
          />
          <CompactSelect
            value={selectedSeverity}
            onChange={setSelectedSeverity}
            options={SEVERITY_OPTIONS}
            minWidth={130}
          />
          <CompactSelect
            value={selectedFixLayer}
            onChange={setSelectedFixLayer}
            options={FIX_LAYER_OPTIONS}
            minWidth={140}
          />

          {hasActiveFilters && (
            <Tooltip title="Clear all filters" arrow>
              <Button
                size="small"
                variant="text"
                onClick={clearAllFilters}
                startIcon={
                  <Iconify icon="mdi:filter-remove-outline" width={14} />
                }
                sx={{
                  height: 32,
                  fontSize: "12px",
                  color: "text.secondary",
                  minWidth: 0,
                  px: 1,
                }}
              >
                Clear
              </Button>
            </Tooltip>
          )}
        </Stack>

        {/* Right: bulk actions */}
        <Stack direction="row" alignItems="center" gap={1}>
          {selected.length > 0 && (
            <BulkActionMenu
              count={selected.length}
              selected={selected}
              onClear={onClearSelection}
            />
          )}
        </Stack>
      </Stack>
    </Stack>
  );
}

ErrorFeedFilters.propTypes = {
  selected: PropTypes.array,
  onClearSelection: PropTypes.func,
};
