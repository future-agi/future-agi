import React, { useMemo, useState } from "react";
import {
  Button,
  Checkbox,
  Divider,
  IconButton,
  InputAdornment,
  Menu,
  MenuItem,
  OutlinedInput,
  Select,
  Stack,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SelectionBar from "src/components/selection-bar";
import {
  useObserveProjectList,
  useUpdateErrorFeedIssue,
} from "src/api/errorFeed/mockAdapter";
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

const SOURCE_OPTIONS = [
  { value: "", label: "All Sources" },
  { value: "scanner", label: "Scanner" },
  { value: "eval", label: "Eval" },
];

// ── compact select ─────────────────────────────────────────────────────────
/* `multiple` (#88): pick several values. The store keeps them as one
   comma-separated string, so a single pick reads the same as before. */
function CompactSelect({
  value,
  onChange,
  options,
  minWidth = 130,
  multiple = false,
  noun = "selected",
}) {
  const picked = multiple ? (value ? value.split(",") : []) : null;
  const labelOf = (v) => options.find((o) => o.value === v)?.label || v;
  return (
    <Select
      value={multiple ? picked : value}
      multiple={multiple}
      onChange={(e) =>
        onChange(
          multiple
            ? (e.target.value || []).filter(Boolean).join(",")
            : e.target.value,
        )
      }
      renderValue={
        multiple
          ? (sel) =>
              !sel.length
                ? options[0]?.label
                : sel.length === 1
                  ? labelOf(sel[0])
                  : `${sel.length} ${noun}`
          : undefined
      }
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
      {(multiple ? options.filter((o) => o.value) : options).map((opt) => (
        <MenuItem key={opt.value} value={opt.value}>
          {multiple && (
            <Checkbox
              size="small"
              checked={picked.includes(opt.value)}
              sx={{ p: 0, mr: 1 }}
            />
          )}
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
  multiple: PropTypes.bool,
  noun: PropTypes.string,
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

// ── main filter bar ────────────────────────────────────────────────────────
export default function ErrorFeedFilters({ selected, onClearSelection }) {
  const {
    searchQuery,
    setSearchQuery,
    selectedProject,
    setSelectedProject,
    selectedSource,
    setSelectedSource,
    selectedStatus,
    setSelectedStatus,
    selectedSeverity,
    setSelectedSeverity,
    timeRange,
    setTimeRange,
  } = useErrorFeedStore();

  const { data: projects } = useObserveProjectList();
  const projectOptions = useMemo(
    () => [{ value: "", label: "All Projects" }, ...(projects ?? [])],
    [projects],
  );

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
            placeholder="Search issues or IDs"
            size="small"
            startAdornment={
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear"
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
                    <Iconify icon="solar:close-linear" width={14} />
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
          {/* Every filter inline, as in production (#63) — few enough to
              read at a glance, so no Filter panel to open. */}
          <CompactSelect
            value={selectedProject}
            onChange={setSelectedProject}
            options={projectOptions}
            minWidth={140}
          />
          <CompactSelect
            multiple
            noun="statuses"
            value={selectedStatus}
            onChange={setSelectedStatus}
            options={STATUS_OPTIONS}
            minWidth={120}
          />
          <CompactSelect
            multiple
            noun="severities"
            value={selectedSeverity}
            onChange={setSelectedSeverity}
            options={SEVERITY_OPTIONS}
            minWidth={120}
          />
          <CompactSelect
            multiple
            noun="sources"
            value={selectedSource}
            onChange={setSelectedSource}
            options={SOURCE_OPTIONS}
            minWidth={110}
          />
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
