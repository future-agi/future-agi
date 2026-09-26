import { Box, Button, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useWatch } from "react-hook-form";

import Iconify from "src/components/iconify";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import axios, { endpoints } from "src/utils/axios";
import {
  CATEGORIES,
  OBSERVATION_TYPE_FIELD,
  SPAN_TYPE_PROPERTY,
  toPanelRows,
  toFormRows,
} from "./alertFilterRows";

const OP_DISPLAY = {
  equals: "is",
  not_equals: "is not",
  in: "is",
  not_in: "is not",
  contains: "contains",
  not_contains: "does not contain",
  greater_than: ">",
  greater_than_or_equal: "≥",
  less_than: "<",
  less_than_or_equal: "≤",
  is_null: "is null",
  is_not_null: "is not null",
};

const FilterChip = ({ filter, labelFor, onRemove }) => (
  <Box
    sx={{
      display: "inline-flex",
      alignItems: "center",
      gap: 0.5,
      px: 0.75,
      py: 0.25,
      border: "1px solid",
      borderColor: "divider",
      borderRadius: "6px",
      minHeight: 30,
    }}
  >
    <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
      {filter.fieldName || filter.field}
    </Typography>
    <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
      {OP_DISPLAY[filter.operator] || filter.operator}
    </Typography>
    <Typography
      sx={{
        fontSize: 12,
        fontWeight: 600,
        maxWidth: 200,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {(Array.isArray(filter.value) ? filter.value : [filter.value])
        .filter((v) => v !== undefined && v !== "")
        .map(labelFor)
        .join(", ")}
    </Typography>
    <Box
      component="button"
      type="button"
      onClick={onRemove}
      aria-label={`Remove ${filter.fieldName || filter.field} filter`}
      sx={{
        display: "inline-flex",
        border: 0,
        p: 0,
        bgcolor: "transparent",
        color: "text.disabled",
        cursor: "pointer",
        "&:hover": { color: "text.primary" },
      }}
    >
      <Iconify icon="mdi:close" width={12} />
    </Box>
  </Box>
);

FilterChip.propTypes = {
  filter: PropTypes.object.isRequired,
  labelFor: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

export default function AlertFilterBar({ control, setValue, projectId }) {
  const buttonRef = useRef();
  const [open, setOpen] = useState(false);

  const formFilters = useWatch({ control, name: "filters" });

  // Passing `properties` also stops the panel fetching its own, which is
  // workspace-scoped and returns nothing for projects without a workspace.
  const { data: attributes = [] } = useQuery({
    queryKey: ["eval-attributes", projectId],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: { filters: JSON.stringify({ project_id: projectId }) },
      }),
    enabled: !!projectId,
    select: (data) => data.data?.result ?? [],
  });

  const properties = useMemo(
    () => [
      SPAN_TYPE_PROPERTY,
      // The API does not say what an attribute holds, so the row carries a
      // Type the user picks, defaulting to text as the old form did.
      ...attributes.map((key) => ({
        id: key,
        name: key,
        category: "attribute",
        rawCategory: "custom_attribute",
        type: "text",
        typeSelectable: true,
        apiColType: "SPAN_ATTRIBUTE",
      })),
    ],
    [attributes],
  );

  const panelFilters = useMemo(
    () => toPanelRows(formFilters || []),
    [formFilters],
  );

  const handleApply = useCallback(
    (next) => {
      setValue("filters", toFormRows(next || []), {
        shouldDirty: true,
        shouldValidate: true,
      });
    },
    [setValue],
  );

  const handleRemove = useCallback(
    (index) => {
      handleApply(panelFilters.filter((_, i) => i !== index));
    },
    [handleApply, panelFilters],
  );

  // Only span types have display names. Attribute values are their own label,
  // and must not be run through the span-type map — an attribute whose value
  // happens to be "agent" is not the Agent span type.
  const labelForField = useCallback(
    (field) =>
      field === OBSERVATION_TYPE_FIELD
        ? (value) => SPAN_TYPE_PROPERTY.choiceLabels[value] ?? String(value)
        : String,
    [],
  );

  return (
    <Box
      sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1 }}
    >
      {panelFilters.map((filter, index) => (
        <FilterChip
          key={`${filter.field}-${index}`}
          filter={filter}
          labelFor={labelForField(filter.field)}
          onRemove={() => handleRemove(index)}
        />
      ))}

      <Button
        ref={buttonRef}
        startIcon={<Iconify color="text.primary" icon="material-symbols:add" />}
        onClick={() => setOpen(true)}
        variant="text"
        color="primary"
        size="small"
        sx={{
          fontSize: "12px",
          color: "text.disabled",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          height: "30px",
          px: 1.5,
        }}
      >
        {panelFilters.length > 0 ? "Edit Filters" : "Add Filter"}
      </Button>

      <TraceFilterPanel
        anchorEl={buttonRef?.current}
        open={open}
        onClose={() => setOpen(false)}
        currentFilters={panelFilters}
        onApply={handleApply}
        properties={properties}
        categories={CATEGORIES}
        projectId={projectId}
        showAi={false}
        showQueryTab={false}
        panelWidth={720}
      />
    </Box>
  );
}

AlertFilterBar.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  projectId: PropTypes.string,
};
