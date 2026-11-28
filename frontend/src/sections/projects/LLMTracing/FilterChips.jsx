import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Button, Chip, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import _ from "lodash";
import {
  OBSERVE_SKILL_COPY,
  isSkillFilterItem,
  skillGroupChipText,
  skillGroupOf,
} from "./skillFilters";

// A chip's operator: comparisons as symbols, the rest in the filter panel's
// words.
const OP_LABELS = {
  equals: "=",
  not_equals: "≠",
  is: "is",
  is_not: "is not",
  in: "is one of",
  not_in: "is not one of",
  contains: "contains",
  not_contains: "not contains",
  starts_with: "starts with",
  ends_with: "ends with",
  more_than: ">",
  greater_than: ">",
  greater_than_or_equal: "≥",
  less_than: "<",
  less_than_or_equal: "≤",
  between: "between",
  not_between: "not between",
  is_null: "is null",
  is_not_null: "is not null",
};

// Dates compare in time words, as the panel's date operators read.
const DATE_OP_LABELS = {
  equals: "on",
  not_equals: "not on",
  greater_than: "after",
  greater_than_or_equal: "on or after",
  less_than: "before",
  less_than_or_equal: "on or before",
};

/**
 * Build a human-readable label from an extraFilter (snake_case API format).
 * { column_id, filter_config: { filter_type, filter_op, filter_value } }
 */
function buildChipParts(filter, fieldLabelMap) {
  const field = filter?.column_id;
  if (!field) return null;

  const op = filter?.filter_config?.filter_op || "";
  const val = filter?.filter_config?.filter_value;
  const type = filter?.filter_config?.filter_type;
  // A list of one reads as plain (in)equality.
  const single = Array.isArray(val)
    ? val.length === 1
    : !(typeof val === "string" && val.includes(","));
  const opLabel =
    ((type === "datetime" || type === "date") && DATE_OP_LABELS[op]) ||
    (op === "in" && single
      ? "="
      : op === "not_in" && single
        ? "≠"
        : OP_LABELS[op] || op);

  const valueMap = fieldLabelMap?.[field];
  const resolveOne = (v) => {
    const k = String(v ?? "");
    return valueMap?.[k] ?? k;
  };
  let valueStr;
  if (Array.isArray(val)) {
    valueStr = val.map(resolveOne).join(", ");
  } else if (typeof val === "string" && val.includes(",") && valueMap) {
    // ObserveToolbar serializes multi-value filters as comma-joined strings.
    valueStr = val
      .split(",")
      .map((v) => resolveOne(v.trim()))
      .join(", ");
  } else {
    valueStr = resolveOne(val);
  }

  // `_.startCase` on a UUID mangles it into space-separated chunks
  // ("F 701 B 069 6224 46 E 8 …"). When we don't have a `display_name`
  // for a UUID column, fall back to a truncated-id label so each chip
  // stays visually distinguishable from its siblings — "Column" alone
  // was ambiguous when multiple unlabeled filters were active.
  const UUID_RE =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  let prettyField;
  if (filter?.display_name) {
    prettyField = filter.display_name;
  } else if (UUID_RE.test(field)) {
    // e.g. "f701b069" → readable short id, unambiguous per column
    prettyField = `Column ${field.slice(0, 8)}`;
  } else {
    prettyField = _.startCase(field);
  }

  return { field: prettyField, op: opLabel, value: valueStr };
}

/**
 * Chips in filter order. `SKILL` items (SKILL-TRACES §6.3) become one chip
 * per correlation group, so a Skill chip edits and removes its sub-fields
 * together, and a second group reads "and also loaded…" so the AND between
 * groups is visible (UX-12). A `not_in` group reads "No recorded load of…".
 */
export function buildFilterChips(extraFilters, fieldLabelMap) {
  const chips = [];
  const groupChip = new Map();
  let skillGroupCount = 0;
  (extraFilters || []).forEach((filter, idx) => {
    if (isSkillFilterItem(filter)) {
      const group = skillGroupOf(filter);
      const existing = groupChip.get(group);
      if (existing) {
        existing.indices.push(idx);
        existing.items.push(filter);
        return;
      }
      const chip = {
        kind: "skill",
        _idx: idx,
        indices: [idx],
        items: [filter],
        column_id: filter.column_id,
        andAlso: skillGroupCount > 0,
      };
      skillGroupCount += 1;
      groupChip.set(group, chip);
      chips.push(chip);
      return;
    }
    const parts = buildChipParts(filter, fieldLabelMap);
    if (parts) chips.push({ ...filter, kind: "filter", _idx: idx, indices: [idx], parts });
  });
  return chips.map((chip) => {
    if (chip.kind !== "skill") return chip;
    const { text, tooltip, negative, failed } = skillGroupChipText(chip.items, fieldLabelMap);
    return {
      ...chip,
      negative,
      failed,
      tooltip,
      parts: {
        field: chip.andAlso ? OBSERVE_SKILL_COPY.andAlsoLoaded : OBSERVE_SKILL_COPY.fields["skill.id"],
        op: "",
        value: text,
      },
    };
  });
}

const FilterChips = ({
  extraFilters,
  onRemoveFilter,
  onRemoveFilters,
  onClearAll,
  onSave,
  onAddFilter,
  onChipClick,
  fieldLabelMap,
}) => {
  const chips = useMemo(
    () => buildFilterChips(extraFilters, fieldLabelMap),
    [extraFilters, fieldLabelMap],
  );
  const removeChip = (chip) => {
    if (chip.indices.length === 1) {
      onRemoveFilter(chip._idx);
    } else if (onRemoveFilters) {
      onRemoveFilters(chip.indices);
    } else {
      // Highest index first, so earlier indices stay valid.
      [...chip.indices].sort((a, b) => b - a).forEach((idx) => onRemoveFilter(idx));
    }
  };

  if (chips.length === 0) return null;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        flexWrap: "wrap",
        maxWidth: "100%",
        minWidth: 0,
        overflow: "hidden",
        px: 2,
        py: 0.5,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        minHeight: 36,
      }}
    >
      {/* Filter chips */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          flex: 1,
          flexWrap: "wrap",
          maxWidth: "100%",
          minWidth: 0,
          overflow: "hidden",
        }}
      >
        {chips.map((chip) => (
          <Tooltip
            key={chip._idx}
            title={
              chip.tooltip ||
              `${chip.parts.field} ${chip.parts.op} ${chip.parts.value}`
            }
            placement="top"
            arrow
          >
            <Chip
              size="small"
              data-filter-chip-column={chip.column_id}
              data-filter-chip-kind={chip.kind}
              onDelete={() => removeChip(chip)}
              onClick={
                onChipClick
                  ? (e) => onChipClick(chip._idx, e.currentTarget)
                  : undefined
              }
              clickable={!!onChipClick}
              label={
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 0.5,
                    maxWidth: "100%",
                    minWidth: 0,
                  }}
                >
                  <Typography
                    variant="caption"
                    sx={{
                      flexShrink: 0,
                      color: "text.secondary",
                      maxWidth: 180,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {chip.parts.field}
                  </Typography>
                  {chip.parts.op ? (
                    <Typography
                      variant="caption"
                      sx={{
                        flexShrink: 0,
                        color: "text.disabled",
                      }}
                    >
                      {chip.parts.op}
                    </Typography>
                  ) : null}
                  <Typography
                    variant="caption"
                    fontWeight={600}
                    sx={{
                      minWidth: 0,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      color: chip.failed ? "error.main" : "text.primary",
                    }}
                  >
                    {chip.parts.value}
                  </Typography>
                </Box>
              }
              sx={{
                maxWidth: "100%",
                minWidth: 0,
                bgcolor: "background.neutral",
                border: "1px solid",
                borderColor: "divider",
                transition: (theme) =>
                  theme.transitions.create(
                    ["background-color", "border-color"],
                    {
                      duration: theme.transitions.duration.shortest,
                    },
                  ),
                "&:hover": {
                  bgcolor: "action.hover",
                  borderColor: "text.disabled",
                },
                "& .MuiChip-label": {
                  display: "block",
                  maxWidth: "100%",
                  minWidth: 0,
                  px: 0.75,
                },
                "& .MuiChip-deleteIcon": {
                  flexShrink: 0,
                  color: "text.secondary",
                  "&:hover": { color: "text.primary" },
                },
              }}
            />
          </Tooltip>
        ))}

        {/* Add filter button — opens filter popup */}
        <Box
          component="button"
          aria-label="Add filter"
          onClick={(e) => onAddFilter?.(e.currentTarget)}
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 24,
            height: 24,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            bgcolor: "background.paper",
            color: "text.secondary",
            cursor: "pointer",
            p: 0,
            "&:hover": {
              color: "text.primary",
              bgcolor: "action.hover",
              borderColor: "text.disabled",
            },
          }}
        >
          <Iconify icon="solar:add-linear" width={14} />
        </Box>
      </Box>

      {/* Clear + Save buttons */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          flexShrink: 0,
          ml: "auto",
        }}
      >
        <Button
          size="small"
          data-filter-chips-action="clear"
          onClick={onClearAll}
          sx={{
            color: "text.secondary",
            minWidth: "auto",
            p: 0,
            "&:hover": { color: "text.primary", bgcolor: "transparent" },
          }}
        >
          Clear
        </Button>
        {onSave && (
          <Button
            size="small"
            onClick={onSave}
            sx={{
              color: "primary.dark",
              minWidth: "auto",
              p: 0,
              "&:hover": { bgcolor: "transparent" },
            }}
          >
            Save
          </Button>
        )}
      </Box>
    </Box>
  );
};

FilterChips.propTypes = {
  extraFilters: PropTypes.array,
  onRemoveFilter: PropTypes.func.isRequired,
  // Removes several items at once (a Skill chip's correlation group). When
  // absent, the group is removed one index at a time, highest first.
  onRemoveFilters: PropTypes.func,
  onClearAll: PropTypes.func.isRequired,
  onSave: PropTypes.func,
  // Called with `(anchorEl)` so the opener can anchor its filter popover
  // next to the `+` button instead of some other element higher up.
  onAddFilter: PropTypes.func,
  // Optional click-to-edit affordance. When provided, chips render as
  // clickable and call `(idx, anchorEl)` so the opener can scroll/open
  // the filter panel onto that row.
  onChipClick: PropTypes.func,
  // { [columnId]: { [value]: label } } — resolves chip display labels for
  // enum-like fields (e.g. Project UUID → project name).
  fieldLabelMap: PropTypes.object,
};

export default React.memo(FilterChips);
