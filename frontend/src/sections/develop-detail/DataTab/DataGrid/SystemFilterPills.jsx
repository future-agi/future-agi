import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  Chip,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import { useGetDatasetDetail } from "src/api/develop/develop-detail";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  INVALID_PILL_LABEL,
  collabPillLabel,
  rowIdsPillLabel,
  sourceKindPillLabel,
  sourceProjectPillLabel,
} from "./dataGridView";
import { useDataGridView } from "./useDataGridView";
import { NEW_ROWS_LABEL } from "./newRowsReveal";
import {
  ROW_STATUS_LABEL,
  useChangeSetStore,
} from "../Versions/changeSetStore";
// W5 grid seam G3: the `Expression: …` pill.
import { expressionPillLabel } from "src/sections/common/filters/expressionPill";

const STATUS_ORDER = ["needs_review", "draft", "approved", "archived"];
const COUNT_PARAMS = { page_size: 1, current_page_index: 0 };

const isTempId = (id) => /^(new|imp)-/.test(String(id ?? ""));

// Active system filters that have no control of their own in the toolbar,
// as removable pills styled like FilterChips: `Errored cells` and the R2
// system filters `Rows (6)`, `Source: Session`, `Project: support-prod`,
// `Invalid rows only`. Failed only and Splits are NOT repeated here: their
// toolbar controls already show the state (red `Failed only · 71`,
// `hard, smoke`) and clear it in one click, and a second row would push
// the grid down on every toggle.

const Pill = ({ icon, label, tooltip, onDelete, active = false }) => (
  <CustomTooltip show title={tooltip} placement="top" size="small" arrow>
    <Chip
      size="small"
      onDelete={onDelete}
      deleteIcon={<Iconify icon="mdi:close" width={14} />}
      label={
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Iconify
            icon={icon}
            width={14}
            sx={{ color: active ? "primary.main" : "text.secondary" }}
          />
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: active ? "primary.main" : "text.primary",
            }}
          >
            {label}
          </Typography>
        </Box>
      }
      sx={{
        height: 26,
        bgcolor: active ? "action.selected" : "background.neutral",
        border: "1px solid",
        borderColor: active ? "primary.main" : "divider",
        borderRadius: "6px",
        "& .MuiChip-label": { px: 0.75 },
        "& .MuiChip-deleteIcon": {
          color: "text.secondary",
          "&:hover": { color: "text.primary" },
        },
      }}
    />
  </CustomTooltip>
);

Pill.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  tooltip: PropTypes.string,
  onDelete: PropTypes.func,
  active: PropTypes.bool,
};

const SystemFilterPills = ({ datasetId }) => {
  const {
    cellErrorOnly,
    setCellErrorOnly,
    rowIds,
    rowIdsLabel,
    sourceKind,
    sourceProject,
    sourceProjectName,
    invalidOnly,
    statuses,
    tags,
    setTags,
    collab,
    setCollabView,
    setRowIds,
    setSourceKind,
    setSourceProject,
    setInvalidOnly,
    setStatuses,
    expression,
    setExpression,
  } = useDataGridView(datasetId);
  // W3-3: `Status ▾` (rows per status, archived included) once any row is
  // not plain Approved; `Status: Archived` while it filters.
  const [statusAnchor, setStatusAnchor] = useState(null);
  const { data: countsData } = useGetDatasetDetail(datasetId, {}, COUNT_PARAMS);
  const statusCounts = countsData?.metadata?.counts?.status ?? null;
  const tagCounts = countsData?.metadata?.counts?.tags ?? {};
  const tagNames = Object.keys(tagCounts).sort();
  const [tagAnchor, setTagAnchor] = useState(null);
  const showTags = (tags ?? []).length > 0 || tagNames.length > 0;
  const showStatus =
    (statuses ?? []).length > 0 ||
    Boolean(
      statusCounts &&
        STATUS_ORDER.some((k) => k !== "approved" && statusCounts[k] > 0),
    );
  // IT-R2 G5 P4: `New (6)` names the rows an import just added; once those
  // pending rows are gone (discarded), the pill clears itself.
  const ops = useChangeSetStore((st) => st.sets?.[datasetId]?.ops);
  const isNewView = rowIdsLabel === NEW_ROWS_LABEL;
  const stale =
    isNewView &&
    rowIds.length > 0 &&
    rowIds.every((id) => isTempId(id)) &&
    !rowIds.some((id) =>
      (ops ?? []).some((o) => o.op === "add_row" && o.temp_id === id),
    );
  useEffect(() => {
    if (stale) setRowIds([]);
  }, [stale, setRowIds]);
  const r2Active =
    Boolean(collab?.key) ||
    rowIds.length > 0 ||
    sourceKind ||
    sourceProject ||
    invalidOnly ||
    showStatus ||
    showTags ||
    !!expression;
  if (!cellErrorOnly && !r2Active) return null;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        flexWrap: "wrap",
        px: 2,
        py: 0.5,
        minHeight: 36,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      {cellErrorOnly && (
        <Pill
          icon="mdi:alert-circle-outline"
          label="Errored cells"
          tooltip="Rows with a generated cell in error"
          onDelete={() => setCellErrorOnly(false)}
        />
      )}
      {/* W4 §9 (M6): a live collaboration view (comments, assignments,
          language), answered by the server at query time. */}
      {collab?.key && (
        <Pill
          icon="mdi:account-multiple-outline"
          label={collab.label ?? collabPillLabel(collab)}
          tooltip="Updates as comments, assignments and rows change"
          active
          onDelete={() => setCollabView(null)}
        />
      )}
      {rowIds.length > 0 && (
        <Pill
          icon={isNewView ? "mdi:new-box" : "mdi:format-list-checks"}
          label={rowIdsPillLabel(rowIds, rowIdsLabel)}
          tooltip={
            isNewView
              ? "The rows you just added. × shows every row."
              : "Only these rows"
          }
          active={isNewView}
          onDelete={() => setRowIds([])}
        />
      )}
      {sourceKind && (
        <Pill
          icon="mdi:import"
          label={sourceKindPillLabel(sourceKind)}
          tooltip="Rows imported from this kind of source"
          onDelete={() => setSourceKind(null)}
        />
      )}
      {sourceProject && (
        <Pill
          icon="mdi:folder-outline"
          label={sourceProjectPillLabel(sourceProject, sourceProjectName)}
          tooltip="Rows imported from this Observe project"
          onDelete={() => setSourceProject(null)}
        />
      )}
      {invalidOnly && (
        <Pill
          icon="mdi:alert-outline"
          label={INVALID_PILL_LABEL}
          tooltip="Rows whose values fail their column schema"
          onDelete={() => setInvalidOnly(false)}
        />
      )}
      {showStatus && (
        <>
          <Chip
            size="small"
            variant={statuses?.length ? "filled" : "outlined"}
            color={statuses?.length ? "primary" : "default"}
            data-testid="status-filter-chip"
            icon={<Iconify icon="mdi:list-status" width={14} />}
            label={
              statuses?.length
                ? `Status: ${statuses.map((st) => ROW_STATUS_LABEL[st] ?? st).join(", ")}`
                : `Status${statusCounts?.archived ? ` · ${statusCounts.archived} archived` : ""}`
            }
            onClick={(e) => setStatusAnchor(e.currentTarget)}
            onDelete={statuses?.length ? () => setStatuses([]) : undefined}
            sx={{ height: 24, typography: "s3" }}
          />
          <Menu
            anchorEl={statusAnchor}
            open={Boolean(statusAnchor)}
            onClose={() => setStatusAnchor(null)}
          >
            {STATUS_ORDER.map((st) => {
              const on = (statuses ?? []).includes(st);
              return (
                <MenuItem
                  key={st}
                  dense
                  onClick={() =>
                    setStatuses(
                      on
                        ? statuses.filter((x) => x !== st)
                        : [...(statuses ?? []), st],
                    )
                  }
                >
                  <Checkbox size="small" checked={on} sx={{ p: 0.25, mr: 1 }} />
                  <ListItemText
                    primary={ROW_STATUS_LABEL[st]}
                    secondary={
                      statusCounts ? String(statusCounts[st] ?? 0) : null
                    }
                    primaryTypographyProps={{ typography: "s2" }}
                    secondaryTypographyProps={{ typography: "s3" }}
                  />
                </MenuItem>
              );
            })}
          </Menu>
        </>
      )}
      {showTags && (
        <>
          <Chip
            size="small"
            variant={tags?.length ? "filled" : "outlined"}
            color={tags?.length ? "primary" : "default"}
            data-testid="tags-filter-chip"
            icon={<Iconify icon="mdi:tag-multiple-outline" width={14} />}
            label={tags?.length ? `Tags: ${tags.join(", ")}` : "Tags"}
            onClick={(e) => setTagAnchor(e.currentTarget)}
            onDelete={tags?.length ? () => setTags([]) : undefined}
            sx={{ height: 24, typography: "s3" }}
          />
          <Menu
            anchorEl={tagAnchor}
            open={Boolean(tagAnchor)}
            onClose={() => setTagAnchor(null)}
          >
            {tagNames.map((t) => {
              const on = (tags ?? []).includes(t);
              return (
                <MenuItem
                  key={t}
                  dense
                  onClick={() =>
                    setTags(
                      on ? tags.filter((x) => x !== t) : [...(tags ?? []), t],
                    )
                  }
                >
                  <Checkbox size="small" checked={on} sx={{ p: 0.25, mr: 1 }} />
                  <ListItemText
                    primary={t}
                    secondary={String(tagCounts[t] ?? 0)}
                    primaryTypographyProps={{ typography: "s2" }}
                    secondaryTypographyProps={{ typography: "s3" }}
                  />
                </MenuItem>
              );
            })}
          </Menu>
        </>
      )}
      {expression && (
        <Pill
          icon="mdi:code-parentheses"
          label={expressionPillLabel({ expr: expression })}
          tooltip={expression}
          active
          onDelete={() => setExpression(null)}
        />
      )}
    </Box>
  );
};

SystemFilterPills.propTypes = { datasetId: PropTypes.string.isRequired };

export default SystemFilterPills;
