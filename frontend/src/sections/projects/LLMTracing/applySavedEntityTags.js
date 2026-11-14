import { withLiveGridApi } from "src/utils/gridApi";

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

// Effective tags of a row that shares the saved assignment but is another
// physical span (another root of the same trace): its own source tags plus the
// saved manual tags, merged by name as the backend's effective_tags does.
const effectiveTagsFor = (row, manualTags) => {
  const sourceNames = new Set(row.source_tag_names || []);
  const tags = new Map();
  (row.tags || []).forEach((tag) => {
    if (tag?.name && sourceNames.has(tag.name)) tags.set(tag.name, { ...tag });
  });
  sourceNames.forEach((name) => {
    if (!tags.has(name)) tags.set(name, { name });
  });
  (manualTags || []).forEach((tag) => {
    tags.set(tag.name, { ...(tags.get(tag.name) || {}), ...tag });
  });
  return [...tags.values()];
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
  if (!result || !Number.isSafeInteger(result.tag_revision)) return 0;
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
      row.tags = samePhysicalSpan
        ? result.tags
        : effectiveTagsFor(row, result.manual_tags);
      if (samePhysicalSpan && result.source_tag_names !== undefined) {
        row.source_tag_names = result.source_tag_names;
      }
      row.manual_tags = result.manual_tags;
      row.tag_revision = result.tag_revision;
      updated.push(node);
    });
    if (updated.length > 0) {
      liveApi.refreshCells({ rowNodes: updated, force: true });
    }
  });
  return updated.length;
}
