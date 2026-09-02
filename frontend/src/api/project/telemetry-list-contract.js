import {
  TracerObservationSpanListSpansResponse,
  TracerTraceListTracesResponse,
} from "src/generated/api-contracts/api.zod";
import { getSpanPhysicalRowId } from "src/sections/projects/LLMTracing/spanPhysicalIdentity";

const presentColumnConfig = (column) => ({
  ...column,
  isVisible: column.is_visible,
  groupBy: column.group_by,
  outputType: column.output_type,
  reverseOutput: column.reverse_output,
  annotationLabelType: column.annotation_label_type,
  choicesMap: column.choices_map,
  evalTemplateId: column.eval_template_id,
  sourceField: column.source_field,
  parentEvalId: column.parent_eval_id,
});

const presentPrototypeResult = (result, identity) => {
  result.table.forEach((row, index) => {
    if (!row || typeof row !== "object" || !identity.value(row)) {
      throw new Error(
        `Telemetry list row #${index} is missing canonical ${identity.label}`,
      );
    }
  });

  return {
    columnConfig: result.column_config.map(presentColumnConfig),
    table: result.table,
    metadata: result.metadata,
    totalRows: result.metadata.total_rows,
    hasMore:
      typeof result.metadata.has_more === "boolean"
        ? result.metadata.has_more
        : null,
  };
};

/** Validate and project the prototype trace-list HTTP response. */
export const parsePrototypeTraceListResponse = (payload) =>
  parsePrototypeResponse(TracerTraceListTracesResponse.parse(payload), {
    label: "trace_id",
    value: (row) => row.trace_id,
  });

/** Validate and project the prototype span-list HTTP response. */
export const parsePrototypeSpanListResponse = (payload) =>
  parsePrototypeResponse(
    TracerObservationSpanListSpansResponse.parse(payload),
    { label: "span physical identity", value: getSpanPhysicalRowId },
  );

function parsePrototypeResponse(response, identity) {
  if (response.status !== true) {
    throw new Error("Telemetry list response was not successful");
  }
  return presentPrototypeResult(response.result, identity);
}
