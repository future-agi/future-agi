import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Button, Chip, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import _ from "lodash";

/**
 * Build a human-readable label from an extraFilter (snake_case API format).
 * { column_id, filter_config: { filter_type, filter_op, filter_value } }
 */
function buildChipParts(filter, fieldLabelMap) {
  const field = filter?.column_id;
  if (!field) return null;

  const op = filter?.filter_config?.filter_op || "";
  const opLabel =
    {
      equals: "is",
      not_equals: "is not",
      contains: "contains",
      not_contains: "not contains",
      is: "is",
      is_not: "is not",
      in: "is one of",
      not_in: "is not one of",
      more_than: ">",
      less_than: "<",
      between: "between",
    }[op] || op;

  const val = filter?.filter_config?.filter_value;
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

const FilterChips = ({
  extraFilters,
  onRemoveFilter,
  onClearAll,
  onSave,
  onAddFilter,
  onChipClick,
  fieldLabelMap,
}) => {
  const chips = useMemo(
    () =>
      (extraFilters || [])
        .map((f, idx) => ({
          ...f,
          _idx: idx,
          parts: buildChipParts(f, fieldLabelMap),
        }))
        .filter((c) => c.parts),
    [extraFilters, fieldLabelMap],
  );

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
            title={`${chip.parts.field} ${chip.parts.op} ${chip.parts.value}`}
            placement="top"
            arrow
          >
            <Chip
              size="small"
              onDelete={() => onRemoveFilter(chip._idx)}
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
                    sx={{
                      flexShrink: 0,
                      fontSize: 12,
                      color: "text.secondary",
                      maxWidth: 180,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {chip.parts.field}
                  </Typography>
                  <Typography
                    sx={{
                      flexShrink: 0,
                      fontSize: 11,
                      color: "text.disabled",
                    }}
                  >
                    {chip.parts.op}
                  </Typography>
                  <Typography
                    sx={{
                      minWidth: 0,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      fontSize: 12,
                      fontWeight: 600,
                      color: "text.primary",
                    }}
                  >
                    {chip.parts.value}
                  </Typography>
                </Box>
              }
              sx={{
                height: 26,
                maxWidth: "100%",
                minWidth: 0,
                bgcolor: "background.neutral",
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "6px",
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
                  fontSize: 14,
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
          sx={(theme) => ({
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 24,
            height: 24,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            bgcolor:
              theme.palette.mode === "dark"
                ? "rgba(255,255,255,0.04)"
                : "background.paper",
            color: "text.secondary",
            cursor: "pointer",
            p: 0,
            "&:hover": {
              color: "text.primary",
              bgcolor:
                theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.08)"
                  : "action.hover",
              borderColor: "text.disabled",
            },
          })}
        >
          <Iconify icon="mdi:plus" width={14} />
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
          onClick={onClearAll}
          sx={{
            textTransform: "none",
            fontSize: 12,
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
              textTransform: "none",
              fontSize: 12,
              fontWeight: 600,
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
