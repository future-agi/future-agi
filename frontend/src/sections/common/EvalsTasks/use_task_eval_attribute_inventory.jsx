import { useMemo, useState } from "react";
import { Box, Button, Typography } from "@mui/material";
import { getAttributeLookupMessage } from "src/utils/queryReadState";
import {
  mergeTracingFieldNames,
  normalizeExactAttributeRowType,
  useExactEvalAttributeFields,
} from "src/sections/evals/components/useExactEvalAttributeFields";

const TRACE_FIELDS = [
  "input",
  "output",
  "name",
  "error",
  "tags",
  "metadata",
  "external_id",
];

const SPAN_FIELDS = [
  "latency_ms",
  "prompt_tokens",
  "completion_tokens",
  "total_tokens",
  "cost",
  "response_time",
  "model",
  "name",
  "observation_type",
  "status",
  "status_message",
  "provider",
];

/**
 * Return deterministic resolver paths that do not require a data scan.
 *
 * Trace/session mappings expose the canonical first child position. The
 * mapping control remains free-text, so callers can still enter a later
 * position explicitly without publishing sampled cardinality as an exact
 * inventory.
 */
export function canonicalTaskEvalFields(rowType) {
  const normalized = normalizeExactAttributeRowType(rowType);
  if (normalized === "traces") {
    return [...TRACE_FIELDS, ...SPAN_FIELDS.map((field) => `spans.0.${field}`)];
  }
  if (normalized === "sessions") {
    return [
      "name",
      "bookmarked",
      ...TRACE_FIELDS.map((field) => `traces.0.${field}`),
      ...SPAN_FIELDS.map((field) => `traces.0.spans.0.${field}`),
    ];
  }
  return SPAN_FIELDS;
}

export function useTaskEvalAttributeInventory({ projectId, rowType, enabled }) {
  const [search, setSearch] = useState("");
  const inventory = useExactEvalAttributeFields({
    projectId,
    rowType,
    search,
    enabled: enabled && Boolean(projectId),
  });
  const sourceColumns = useMemo(
    () =>
      mergeTracingFieldNames(
        canonicalTaskEvalFields(rowType),
        inventory.data,
      ).map((field) => ({
        headerName: field,
        field,
        name: field,
      })),
    [inventory.data, rowType],
  );
  const message = getAttributeLookupMessage(inventory.queryReadState);
  const inventoryControls =
    message || inventory.hasNextPage || inventory.isFetching ? (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1,
          mb: 0.75,
        }}
      >
        <Typography variant="caption" color="text.secondary">
          {message ||
            (inventory.isFetching && !inventory.hasNextPage
              ? "Loading attributes…"
              : "Browse the complete retained attribute inventory.")}
        </Typography>
        {inventory.hasNextPage && (
          <Button
            size="small"
            variant="text"
            disabled={inventory.isFetchingNextPage}
            onClick={() =>
              inventory.fetchNextPage?.()?.catch?.(() => undefined)
            }
            sx={{ flexShrink: 0, minWidth: 0, px: 0.5, fontSize: 11 }}
          >
            {inventory.isFetchingNextPage
              ? "Loading…"
              : inventory.isFetchNextPageError
                ? "Retry"
                : "Load more"}
          </Button>
        )}
      </Box>
    ) : null;

  return {
    sourceColumns,
    attributeFields: inventory.data || [],
    onSourceColumnSearchChange: setSearch,
    sourceColumnInventoryControls: inventoryControls,
  };
}
