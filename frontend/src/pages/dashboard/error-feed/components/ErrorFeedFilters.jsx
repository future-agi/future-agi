import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Divider,
  IconButton,
  InputAdornment,
  Menu,
  MenuItem,
  OutlinedInput,
  Popover,
  Select,
  Stack,
  Tab,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SelectionBar from "src/components/selection-bar";
import { FilterButton } from "src/components/filter";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { useUpdateErrorFeedIssue } from "src/api/errorFeed/error-feed";
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

const SOURCE_OPTIONS = [
  { value: "", label: "All Sources" },
  { value: "scanner", label: "Scanner" },
  { value: "eval", label: "Eval" },
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

// Filter panel fields. Each maps to one store value (a single selection), so
// the panel rows and the store stay interchangeable.
const withoutAll = (options) => options.filter((opt) => opt.value !== "");

const buildFilterFields = (projectOptions) => [
  {
    id: "project",
    label: "Project",
    type: "enum",
    options: withoutAll(projectOptions),
    operators: ["equals"],
  },
  {
    id: "status",
    label: "Status",
    type: "enum",
    options: withoutAll(STATUS_OPTIONS),
    operators: ["equals"],
  },
  {
    id: "severity",
    label: "Severity",
    type: "enum",
    options: withoutAll(SEVERITY_OPTIONS),
    operators: ["equals"],
  },
  {
    id: "fix_layer",
    label: "Fix layer",
    type: "enum",
    options: withoutAll(FIX_LAYER_OPTIONS),
    operators: ["equals"],
  },
  {
    id: "source",
    label: "Source",
    type: "enum",
    options: withoutAll(SOURCE_OPTIONS),
    operators: ["equals"],
  },
];

// ── searchable multi-select project picker with checkboxes ─────────────────
function ProjectMultiSelect({ selectedValues, onChange, options }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [query, setQuery] = useState("");
  const open = Boolean(anchorEl);

  const projectItems = useMemo(
    () => options.filter((opt) => opt.value !== ""),
    [options],
  );
  const filteredItems = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return projectItems;
    return projectItems.filter((opt) =>
      String(opt.label || "").toLowerCase().includes(q),
    );
  }, [projectItems, query]);

  const latestSelectedRef = useRef(selectedValues);
  useEffect(() => {
    latestSelectedRef.current = selectedValues;
  }, [selectedValues]);

  const toggleProject = (val) => {
    const current = latestSelectedRef.current;
    const next = current.includes(val)
      ? current.filter((v) => v !== val)
      : [...current, val];
    latestSelectedRef.current = next;
    onChange(next);
  };

  const buttonLabel = useMemo(() => {
    if (selectedValues.length === 0) return "All Projects";
    if (selectedValues.length === 1) {
      return (
        projectItems.find((p) => p.value === selectedValues[0])?.label ||
        "1 project"
      );
    }
    return `${selectedValues.length} projects`;
  }, [selectedValues, projectItems]);

  return (
    <>
      <Box
        component="button"
        type="button"
        onClick={(e) => setAnchorEl(e.currentTarget)}
        aria-haspopup="listbox"
        aria-expanded={open}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1,
          height: 32,
          minWidth: 170,
          maxWidth: 230,
          px: 1.25,
          borderRadius: "6px",
          border: "1px solid",
          borderColor:
            selectedValues.length > 0 || open ? "primary.main" : "divider",
          bgcolor:
            selectedValues.length > 0 ? "action.selected" : "background.paper",
          color: "text.primary",
          fontSize: "13px",
          fontFamily: "inherit",
          cursor: "pointer",
          transition: "border-color 120ms, background-color 120ms",
          "&:hover": {
            borderColor:
              selectedValues.length > 0 ? "primary.main" : "text.secondary",
          },
        }}
      >
        <Typography
          component="span"
          noWrap
          sx={{
            fontSize: "13px",
            fontWeight: selectedValues.length > 0 ? 500 : 400,
            color: "text.primary",
          }}
        >
          {buttonLabel}
        </Typography>
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={14}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
      </Box>

      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={() => {
          setAnchorEl(null);
          setQuery("");
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        PaperProps={{
          elevation: 3,
          sx: {
            mt: 0.5,
            width: 276,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            overflow: "hidden",
          },
        }}
      >
        {/* Search box */}
        <Box sx={{ p: 1, borderBottom: "1px solid", borderColor: "divider" }}>
          <OutlinedInput
            autoFocus
            fullWidth
            size="small"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search projects..."
            startAdornment={
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear"
                  width={14}
                  sx={{ color: "text.secondary" }}
                />
              </InputAdornment>
            }
            endAdornment={
              query ? (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    aria-label="Clear project search"
                    onClick={() => setQuery("")}
                    edge="end"
                  >
                    <Iconify icon="solar:close-linear" width={13} />
                  </IconButton>
                </InputAdornment>
              ) : null
            }
            sx={{
              height: 30,
              fontSize: "12.5px",
              borderRadius: "6px",
              "& .MuiOutlinedInput-notchedOutline": { borderColor: "divider" },
            }}
            notched={false}
          />
        </Box>

        {/* Quick actions row */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{
            px: 1.25,
            py: 0.6,
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: "action.hover",
          }}
        >
          <Typography typography="s3" color="text.secondary">
            {selectedValues.length > 0
              ? `${selectedValues.length} selected`
              : `${projectItems.length} projects`}
          </Typography>
          <Stack direction="row" gap={0.75}>
            <Button
              size="small"
              variant="text"
              onClick={() =>
                onChange(filteredItems.map((opt) => opt.value))
              }
              sx={{ minWidth: 0, px: 0.5, py: 0, color: "primary.main" }}
            >
              Select all
            </Button>
            {selectedValues.length > 0 && (
              <Button
                size="small"
                variant="text"
                onClick={() => onChange([])}
                sx={{ minWidth: 0, px: 0.5, py: 0, color: "text.secondary" }}
              >
                Clear
              </Button>
            )}
          </Stack>
        </Stack>

        {/* Scrollable checkbox list */}
        <Box sx={{ maxHeight: 260, overflowY: "auto", py: 0.5 }}>
          {filteredItems.length === 0 ? (
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ px: 1.5, py: 2, textAlign: "center" }}
            >
              No matching projects
            </Typography>
          ) : (
            filteredItems.map((opt) => {
              const checked = selectedValues.includes(opt.value);
              return (
                <MenuItem
                  key={opt.value}
                  dense
                  onClick={() => toggleProject(opt.value)}
                  sx={{ px: 1, py: 0.45, gap: 0.5 }}
                >
                  <Checkbox
                    size="small"
                    checked={checked}
                    sx={{ p: 0.35 }}
                    tabIndex={-1}
                    disableRipple
                  />
                  <Typography
                    typography="s2"
                    color="text.primary"
                    noWrap
                    sx={{ flex: 1, minWidth: 0 }}
                  >
                    {opt.label}
                  </Typography>
                </MenuItem>
              );
            })
          )}
        </Box>
      </Popover>
    </>
  );
}
ProjectMultiSelect.propTypes = {
  selectedValues: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.string }),
  ).isRequired,
};

// ── compact select ─────────────────────────────────────────────────────────
function CompactSelect({ value, onChange, options, minWidth = 130 }) {
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
          borderColor: "text.disabled",
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
    {
      label: "Mark as Resolved",
      icon: "solar:check-circle-bold",
      status: "resolved",
    },
    {
      label: "Mark as Acknowledged",
      icon: "solar:check-circle-linear",
      status: "acknowledged",
    },
    {
      label: "Mark as For Review",
      icon: "solar:eye-linear",
      status: "for_review",
    },
    {
      label: "Mark as Escalating",
      icon: "solar:graph-up-linear",
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
      <SelectionBar
        count={count}
        noun="issue"
        onCancel={onClear}
        actions={[
          {
            label: "Bulk actions",
            node: (
              <Button
                size="small"
                variant="text"
                color="inherit"
                endIcon={
                  <Iconify icon="solar:alt-arrow-down-linear" width={16} />
                }
                onClick={(e) => setAnchorEl(e.currentTarget)}
                sx={{
                  color: "text.secondary",
                  "&:hover": { color: "text.primary" },
                }}
              >
                Bulk actions
              </Button>
            ),
          },
        ]}
      />
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
            sx={{ gap: 1 }}
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

const DEFAULT_PROJECT_OPTIONS = [{ value: "", label: "All Projects" }];

// ── main filter bar ────────────────────────────────────────────────────────
// The project options come from the page (TH-8209): the view owns the single
// Observe catalog subscription so the picker and the empty-state decision
// never disagree about freshness.
export default function ErrorFeedFilters({
  selected,
  onClearSelection,
  projectOptions = DEFAULT_PROJECT_OPTIONS,
  stats = null,
}) {
  const {
    searchQuery,
    setSearchQuery,
    selectedProject,
    setSelectedProject,

    selectedStatus,
    setSelectedStatus,
    selectedSeverity,
    setSelectedSeverity,
    selectedSource,
    setSelectedSource,
    selectedFixLayer,
    setSelectedFixLayer,
    timeRange,
    setTimeRange,
  } = useErrorFeedStore();

  const selectedProjectIds = useMemo(
    () =>
      selectedProject
        ? String(selectedProject)
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : [],
    [selectedProject],
  );
  const handleProjectsChange = (ids) => {
    setSelectedProject(ids.join(","));
  };

  const filterFields = useMemo(
    () => buildFilterFields(projectOptions),
    [projectOptions],
  );
  const filterValue = useMemo(
    () =>
      [
        ["project", selectedProjectIds],
        ["status", selectedStatus ? [selectedStatus] : []],
        ["severity", selectedSeverity ? [selectedSeverity] : []],
        ["fix_layer", selectedFixLayer ? [selectedFixLayer] : []],
        ["source", selectedSource ? [selectedSource] : []],
      ]
        .filter(([, vals]) => vals.length > 0)
        .map(([field, vals]) => ({
          field,
          fieldType: "categorical",
          operator: "equals",
          value: vals,
        })),
    [
      selectedProjectIds,
      selectedStatus,
      selectedSeverity,
      selectedFixLayer,
      selectedSource,
    ],
  );
  const handleFilterChange = (rows) => {
    const valuesOf = (field) =>
      (rows || []).find((row) => row.field === field)?.value ?? [];
    setSelectedProject(valuesOf("project").join(","));
    setSelectedStatus(valuesOf("status")[0] ?? "");
    setSelectedSeverity(valuesOf("severity")[0] ?? "");
    setSelectedFixLayer(valuesOf("fix_layer")[0] ?? "");
    setSelectedSource(valuesOf("source")[0] ?? "");
  };

  const quickViews = useMemo(
    () => [
      { value: "", label: "All", count: stats?.total_errors ?? null },
      { value: "escalating", label: "Escalating", count: stats?.escalating ?? null },
      { value: "for_review", label: "For review", count: stats?.for_review ?? null },
      { value: "resolved", label: "Resolved", count: stats?.resolved ?? null },
    ],
    [stats],
  );
  const segmentedValue = quickViews.some((q) => q.value === (selectedStatus || ""))
    ? selectedStatus || ""
    : false;

  return (
    <Stack gap={1.25}>
      {/* Unified single toolbar: Search + Filters on left, native SegmentedTabs queue switch (or Bulk actions) on right */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1.5}
        flexWrap="wrap"
      >
        <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
          {/* Search */}
          <OutlinedInput
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search issues"
            size="small"
            startAdornment={
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear"
                  width={16}
                  sx={{ color: "text.secondary" }}
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
                    <Iconify icon="solar:close-linear" width={14} />
                  </IconButton>
                </InputAdornment>
              ) : null
            }
            sx={{
              height: 32,
              width: 240,
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
          {projectOptions.length > 1 && (
            <ProjectMultiSelect
              selectedValues={selectedProjectIds}
              onChange={handleProjectsChange}
              options={projectOptions}
            />
          )}
          <CompactSelect
            value={timeRange}
            onChange={setTimeRange}
            options={TIME_RANGE_OPTIONS}
            minWidth={132}
          />
          <FilterButton
            fields={filterFields}
            value={filterValue}
            onChange={handleFilterChange}
          />
        </Stack>

        {/* Right: bulk actions when rows selected; otherwise native Future AGI SegmentedTabs queue switch */}
        <Stack direction="row" alignItems="center" gap={1}>
          {selected.length > 0 ? (
            <BulkActionMenu
              count={selected.length}
              selected={selected}
              onClear={onClearSelection}
            />
          ) : (
            <SegmentedTabs
              value={segmentedValue}
              onChange={(_, val) => setSelectedStatus(val)}
            >
              {quickViews.map((item) => (
                <Tab
                  key={item.value || "all"}
                  value={item.value}
                  label={
                    <Stack direction="row" alignItems="center" gap={0.6}>
                      <span>{item.label}</span>
                      {item.count != null && (
                        <Typography
                          component="span"
                          typography="s3"
                          color={
                            item.value === "escalating" && item.count > 0
                              ? "status.danger.fg"
                              : "text.tertiary"
                          }
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                        >
                          {item.count.toLocaleString()}
                        </Typography>
                      )}
                    </Stack>
                  }
                />
              ))}
            </SegmentedTabs>
          )}
        </Stack>
      </Stack>
    </Stack>
  );
}

ErrorFeedFilters.propTypes = {
  selected: PropTypes.array,
  onClearSelection: PropTypes.func,
  projectOptions: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.string }),
  ),
  stats: PropTypes.shape({
    total_errors: PropTypes.number,
    escalating: PropTypes.number,
    for_review: PropTypes.number,
    acknowledged: PropTypes.number,
    resolved: PropTypes.number,
  }),
};
