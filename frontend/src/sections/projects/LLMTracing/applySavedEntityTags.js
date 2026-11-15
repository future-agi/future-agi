import { withLiveGridApi } from "src/utils/gridApi";
import {
  effectiveEntityTags,
  hasTagMetadata,
} from "src/components/traceDetail/entityTagState";
import { boundObserveListRow } from "./observeListPayload";

const PHYSICAL_FIELDS = [
  "project_id",
  "trace_id",
  "span_id",
  "start_hour",
  "observation_type",
  "service_name",
];

// A root span row (parent_span_id present and empty). Rows without the field
// are not assumed to be roots.
const isRootSpan = (row) =>
  Boolean(row) &&
  Object.prototype.hasOwnProperty.call(row, "parent_span_id") &&
  (row.parent_span_id == null || row.parent_span_id === "");

const physicalKey = (target) =>
  JSON.stringify(PHYSICAL_FIELDS.map((field) => target?.[field] ?? null));

// The assignment a row's tags and tag_revision come from, as the backend keys
// it (tracer/services/entity_tag_reads.py tag_assignment_key): a trace row and
// every root span of that trace share one trace assignment; any other span has
// its own, keyed by its full physical identity.
const assignmentKey = (target, traceAssignment) => {
  if (
    typeof target?.project_id !== "string" ||
    typeof target?.trace_id !== "string"
  ) {
    return null;
  }
  return traceAssignment
    ? JSON.stringify(["trace", target.project_id, target.trace_id])
    : JSON.stringify(["span", physicalKey(target)]);
};

/**
 * Apply an acknowledged tag save to the loaded grid rows of that assignment,
 * without reading the list again.
 *
 * A list refresh would reset the cursor pagination to page 1, and re-reading
 * pages in place can fail closed when their boundaries move. Updating the rows
 * keeps the page, the scroll position and the other rows, and gives each row
 * the acknowledged tag_revision so its next grid edit is not refused as stale.
 *
 * The row objects are updated in place on purpose: the cursor pagination keeps
 * the same row objects for completed pages, so a block that AG Grid reads again
 * from that cache (after a cell edit or an eviction) carries the saved tags.
 *
 * @param api the grid api
 * @param result the tag write's result (tags, manual_tags, source_tag_names,
 *   tag_revision)
 * @param entity the entity that was saved (its tag_target is the write target)
 * @param type "trace" for a trace write, "span" for a span write
 * @param gridKind "trace" when every row is a trace row, else "span"
 * @returns the number of rows updated
 */
export default function applySavedEntityTags(
  api,
  { result, entity, type, gridKind } = {},
) {
  if (!hasTagMetadata(result)) return 0;
  const target = entity?.tag_target;
  const savedKey = assignmentKey(
    target,
    type === "trace" || isRootSpan(entity),
  );
  if (savedKey === null) return 0;
  const savedPhysical = physicalKey(target);
  const updated = [];
  withLiveGridApi(api, (liveApi) => {
    liveApi.forEachNode((node) => {
      const row = node?.data;
      const rowTarget = row?.tag_target;
      const rowKey = assignmentKey(
        rowTarget,
        gridKind === "trace" || isRootSpan(row),
      );
      if (rowKey !== savedKey) return;
      // Never move a row back to an older revision than it already shows.
      if (
        Number.isSafeInteger(row.tag_revision) &&
        row.tag_revision > result.tag_revision
      ) {
        return;
      }
      const samePhysicalSpan = physicalKey(rowTarget) === savedPhysical;
      const state = {
        ...row,
        source_tag_names: samePhysicalSpan
          ? result.source_tag_names
          : row.source_tag_names,
        manual_tags: result.manual_tags,
        tag_revision: result.tag_revision,
      };
      // A bounded sibling may lack complete source metadata. Leave it locked
      // until a detail read supplies that metadata; never copy the saved root.
      if (!hasTagMetadata(state)) return;
      Object.assign(
        row,
        boundObserveListRow({
          tags: effectiveEntityTags(state),
          source_tag_names: state.source_tag_names,
          manual_tags: state.manual_tags,
          tag_revision: state.tag_revision,
        }),
      );
      updated.push(node);
    });
    if (updated.length > 0) {
      liveApi.refreshCells({ rowNodes: updated, force: true });
    }
  });
  return updated.length;
}
