import { withLiveGridApi } from "src/utils/gridApi";

// The fields a tag write returns (EntityTagsResultSerializer). The row keeps
// its own tag_target: the write does not change the physical source.
const TAG_RESULT_FIELDS = [
  "tags",
  "manual_tags",
  "source_tag_names",
  "tag_revision",
];

// Rows on one physical span share one assignment. A trace row and its root
// span row both carry the root span's target and the trace assignment, so a
// save from the drawer on either applies to both grids' rows for that span.
const sameTagTarget = (rowTarget, savedTarget) =>
  Boolean(rowTarget && savedTarget) &&
  typeof savedTarget.span_id === "string" &&
  rowTarget.project_id === savedTarget.project_id &&
  rowTarget.trace_id === savedTarget.trace_id &&
  rowTarget.span_id === savedTarget.span_id;

/**
 * Apply a tag save made outside the grid (the trace detail drawer) to the
 * loaded rows of that entity, without reading the list again.
 *
 * A list refresh would reset the cursor pagination to page 1, and re-reading
 * pages in place can fail closed when their boundaries move. Updating the row
 * keeps the page, the scroll position and the other rows, and gives the row
 * the acknowledged tag_revision so the next grid edit is not refused as stale.
 *
 * The row object is updated in place on purpose: the cursor pagination keeps
 * the same row objects for completed pages, so a block that AG Grid evicts and
 * reads again from that cache also carries the saved tags.
 *
 * Returns the number of rows updated.
 */
export default function applySavedEntityTags(api, { target, result } = {}) {
  if (!result || !Number.isSafeInteger(result.tag_revision)) return 0;
  const updated = [];
  withLiveGridApi(api, (liveApi) => {
    liveApi.forEachNode((node) => {
      const row = node?.data;
      if (!sameTagTarget(row?.tag_target, target)) return;
      // Never move a row back to an older revision than it already shows.
      if (
        Number.isSafeInteger(row.tag_revision) &&
        row.tag_revision > result.tag_revision
      ) {
        return;
      }
      TAG_RESULT_FIELDS.forEach((field) => {
        if (result[field] !== undefined) row[field] = result[field];
      });
      updated.push(node);
    });
    if (updated.length > 0) {
      liveApi.refreshCells({ rowNodes: updated, force: true });
    }
  });
  return updated.length;
}
